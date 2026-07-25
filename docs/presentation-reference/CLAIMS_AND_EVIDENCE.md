# Claims and evidence

This register is the source-control layer for public documentation and
presentation claims. A claim is usable only when its evidence status is
**accepted** and its wording stays within the stated boundary.

| ID | Approved public wording | Evidence | Status and boundary |
| --- | --- | --- | --- |
| C01 | dUMI is an independently maintained fork of UMICollapse. | [`PROVENANCE.md`](../../PROVENANCE.md) and Git history | **Accepted.** Do not imply canonical upstream endorsement. |
| C02 | The canonical upstream baseline is commit `efeab35`; the incorporated `aeacd82` change comes from unmerged upstream PR #32. | Canonical upstream Git history, [PR #32](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/pull/32), and [`PROVENANCE.md`](../../PROVENANCE.md) | **Accepted.** Do not call `aeacd82` a canonical upstream v1.1.0 release. |
| C03 | Canonical upstream's default SAM/BAM route retains alignment groups until input reading is complete. | `DeduplicateSAM.java` at canonical commit `efeab35` and [`ARCHITECTURE.md`](../ARCHITECTURE.md) | **Accepted.** `--two-pass` is a distinct alternative and must be acknowledged. |
| C04 | dUMI adds guarded streaming for compatible coordinate-sorted, single-end SAM/BAM input. | [`DeduplicateSAM.java`](../../src/umicollapse/main/DeduplicateSAM.java), [`Main.java`](../../src/umicollapse/main/Main.java), and streaming tests | **Accepted.** State the eligibility restrictions. |
| C05 | Streaming uses a coordinate-window-bounded working set rather than retaining all alignment groups. | Streaming active-map/priority-queue implementation and [`ARCHITECTURE.md`](../ARCHITECTURE.md) | **Accepted.** Not a constant-memory or absolute bounded-memory claim. |
| C06 | `auto` validates runtime order and clipping, discards an unsafe temporary output, and retries legacy. | [`DeduplicateSAM.java`](../../src/umicollapse/main/DeduplicateSAM.java) and false-order/clipping fixtures | **Accepted.** Forced `on` fails instead of retrying. |
| C07 | Tested FASTQ, SAM, and BAM command-line routes stage output and preserve an existing destination after the exercised processing failures. | [`OutputTransaction.java`](../../src/umicollapse/main/OutputTransaction.java), [`TestResourceAndBoundsRegressions.java`](../../src/test/TestResourceAndBoundsRegressions.java), [`test-cli.sh`](../../test/test-cli.sh), and [`test-streaming.sh`](../../test/test-streaming.sh) | **Accepted.** This covers the advertised routes and failure matrix exercised by the release gate, including same-file and hard-link rejection; it is not a claim about every possible storage failure. |
| C08 | The default underscore UMI path avoids regex substring and uppercase allocations, while custom separators are literal. | [`SAMRead.java`](../../src/umicollapse/util/SAMRead.java), [`Utils.java`](../../src/umicollapse/util/Utils.java), [`TestOptimizedRegressions.java`](../../src/test/TestOptimizedRegressions.java), and [`TestReleaseRegressions.java`](../../src/test/TestReleaseRegressions.java) | **Accepted.** UMIs shorter than the effective length and explicit length zero are rejected rather than silently aliased. No isolated parser speedup is claimed. |
| C09 | Average base quality is calculated lazily. | [`SAMRead.java`](../../src/umicollapse/util/SAMRead.java) and merge-policy parity tests | **Accepted.** Do not attach an isolated speedup without a dedicated benchmark. |
| C10 | The optimized `NgramBKTree` is differentially checked against the `Naive` reference across packed and fallback routes. | [`TestNgramBKTreeRegression.java`](../../src/test/TestNgramBKTreeRegression.java) and [`VALIDATION.md`](../../VALIDATION.md) | **Accepted.** Use the current validation record for any scenario count. |
| C11 | Paired mode incorporates the persistent-reader proposal from upstream PR #32. | Original `aeacd82` commit, [PR #32](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/pull/32), paired hardening tests, and the [paired benchmark](../PERFORMANCE.md#paired-read-result-and-pr-32-attribution) | **Accepted.** The large-reference synthetic paired gain is already present in the PR #32 intermediate and must be attributed to that proposal. The 100-record paired cell is a tiny fixed-cost regression check, not a throughput claim. Both paired fixtures contain unique pairs, so they measure traversal/reference-transition scaling and preservation rather than paired duplicate collapse or representative selection. |
| C12 | dUMI output contains selected representative reads, not newly constructed base-level consensus sequences. | `Merge` implementations and clustering output paths | **Accepted.** Use “representative read” consistently. |
| C13 | Streaming output is declared unsorted because group emission can reorder records. | Writer header handling and sort-order tests in [`test-streaming.sh`](../../test/test-streaming.sh) | **Accepted.** Downstream coordinate indexing requires sorting first. |
| C14 | The build targets Java 11 bytecode, verifies locked dependencies, and embeds a production-source receipt. | [`build.sh`](../../build.sh), [`dependencies.lock`](../../dependencies.lock), [`verify-artifact.sh`](../../scripts/verify-artifact.sh), and [`VALIDATION.md`](../../VALIDATION.md) | **Accepted** for the recorded artifact and gate. |
| C15 | On the four fixed-seed synthetic single-end workloads, default `auto` measured 1.30x–2.75x raw speedups, 15.83%–83.46% peak-RSS reductions, and 1.27x–1.96x raw-plus-ready speedups versus canonical upstream. | [`PERFORMANCE.md`](../PERFORMANCE.md), [`benchmark-summary.csv`](benchmark-summary.csv), and the [clean seven-repetition evidence package](../benchmarks/2026-07-25/README.md) | **Accepted.** Every numeric use must name or visibly encode the workload, mode, stage, synthetic scope, exact comparison, and downstream sort/index treatment. These measurements are not a production-wide guarantee. |
| C16 | Quality-based representative ties and applicable algorithm ties are deterministic; `any` is intentionally arbitrary. | [`MapQualMerge.java`](../../src/umicollapse/merge/MapQualMerge.java), [`AvgQualMerge.java`](../../src/umicollapse/merge/AvgQualMerge.java), algorithm ordering, [`TestReleaseRegressions.java`](../../src/test/TestReleaseRegressions.java), and [`TestThresholdParallelRegressions.java`](../../src/test/TestThresholdParallelRegressions.java) | **Accepted.** Do not describe `any` as deterministic or order-independent. |
| C17 | Paired mate recovery supports indexed BAM, unindexed BAM, and SAM input. | Paired writer implementation, [`TestDeduplicateSAMHardening.java`](../../src/test/TestDeduplicateSAMHardening.java), and [`test-streaming.sh`](../../test/test-streaming.sh) | **Accepted.** This is a compatibility claim. Paired performance must be presented separately, with the tiny-cell tradeoff and PR #32 attribution. |
| C18 | In three post-change Java 21 JFR runs of the one-million-record synthetic sparse workload, no allocation samples were attributed to the three singleton-setup sentinels, while both positive controls were observed. | [`allocation-aggregate.json`](../benchmarks/2026-07-25/profile/allocation-aggregate.json), [`profile-correctness.json`](../benchmarks/2026-07-25/profile/profile-correctness.json), [`profile-receipt.json`](../benchmarks/2026-07-25/profile/profile-receipt.json), and [`PERFORMANCE.md`](../PERFORMANCE.md#final-allocation-profile) | **Accepted.** This is a retained post-change diagnostic, not a before/after profile or performance comparison. Zero sampled weight is not proof that a site can never allocate, especially on multi-UMI groups. |

## Claims that are not approved

Do not state that:

- dUMI is endorsed, released, or maintained by canonical upstream;
- `aeacd82` is canonical UMICollapse v1.1.0;
- profiling discovered or proved the original opportunities;
- the final allocation profile is a before/after comparison or proves that a
  site can never allocate;
- streaming provides constant memory;
- dUMI constructs consensus sequences;
- a synthetic benchmark establishes production-wide performance;
- every UMICollapse mode receives the streaming gain;
- dUMI is orders of magnitude faster than UMICollapse.

## Updating this register

When a code or benchmark change alters a claim:

1. update the canonical technical document first;
2. link the exact test, commit, or result row;
3. narrow the wording to what that evidence supports;
4. update the storyboard only after the status is accepted.
