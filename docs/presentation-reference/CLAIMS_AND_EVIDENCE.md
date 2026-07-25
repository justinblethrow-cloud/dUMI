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
| C07 | Advertised CLI routes stage output and do not replace an existing destination after processing failure. | Transactional output implementation, hardening tests, and `assert_fails_without_replacing` in [`test-streaming.sh`](../../test/test-streaming.sh) | **Pending final acceptance gate.** Narrow to the modes and failures exercised by the accepted matrix. |
| C08 | The default underscore UMI path avoids regex substring and uppercase allocations, while custom separators are literal. | [`SAMRead.java`](../../src/umicollapse/util/SAMRead.java), [`Utils.java`](../../src/umicollapse/util/Utils.java), and parser regressions | **Pending final acceptance gate.** Includes rejection of UMIs shorter than the effective length. |
| C09 | Average base quality is calculated lazily. | [`SAMRead.java`](../../src/umicollapse/util/SAMRead.java) and merge-policy parity tests | **Accepted.** Do not attach an isolated speedup without a dedicated benchmark. |
| C10 | The optimized `NgramBKTree` is differentially checked against the `Naive` reference across packed and fallback routes. | [`TestNgramBKTreeRegression.java`](../../src/test/TestNgramBKTreeRegression.java) and [`VALIDATION.md`](../../VALIDATION.md) | **Accepted.** Use the current validation record for any scenario count. |
| C11 | Paired mode incorporates the persistent-reader proposal from upstream PR #32. | Original `aeacd82` commit, [PR #32](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/pull/32), paired indexed-BAM tests | **Accepted.** No paired-mode performance gain is claimed without a benchmark. |
| C12 | dUMI output contains selected representative reads, not newly constructed base-level consensus sequences. | `Merge` implementations and clustering output paths | **Accepted.** Use “representative read” consistently. |
| C13 | Streaming output is declared unsorted because group emission can reorder records. | Writer header handling and sort-order tests in [`test-streaming.sh`](../../test/test-streaming.sh) | **Accepted.** Downstream coordinate indexing requires sorting first. |
| C14 | The build targets Java 11 bytecode, verifies locked dependencies, and embeds a production-source receipt. | [`build.sh`](../../build.sh), [`dependencies.lock`](../../dependencies.lock), [`verify-artifact.sh`](../../scripts/verify-artifact.sh), and [`VALIDATION.md`](../../VALIDATION.md) | **Accepted** for the recorded artifact and gate. |
| C15 | dUMI is faster or uses less memory than canonical upstream by a stated amount. | [`PERFORMANCE.md`](../PERFORMANCE.md) and [`benchmark-summary.csv`](benchmark-summary.csv) | **Pending final benchmark data.** Do not state a numeric upstream comparison until rows, repetitions, commits, runtime parity, and output equivalence are complete. |
| C16 | Quality-based representative ties and algorithm ties are deterministic; `any` is intentionally arbitrary. | Merge implementations, algorithm ordering, and determinism fixtures | **Pending final acceptance gate.** Do not describe `any` as order-independent. |
| C17 | Paired mate recovery supports indexed BAM, unindexed BAM, and SAM input. | Paired writer implementation and paired hardening fixtures | **Pending final acceptance gate.** This is a compatibility claim, not a paired performance claim. |

## Claims that are not approved

Do not state that:

- dUMI is endorsed, released, or maintained by canonical upstream;
- `aeacd82` is canonical UMICollapse v1.1.0;
- profiling proved the identified opportunities;
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
