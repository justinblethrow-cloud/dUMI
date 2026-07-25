# Optimization rationale and traceability

The changes in this fork began with code-path analysis of the canonical
UMICollapse implementation. This repository does not contain a retained
profiler recording, so the opportunities below should not be described as
profiler findings.

Performance measurements and correctness evidence serve different purposes.
The tests establish behavioral compatibility and safety; benchmarks quantify
only the workloads and configurations they execute.

## Opportunity-to-evidence map

| Upstream mechanism | Opportunity identified | Change in this fork | Behavioral contract | Validation evidence | Performance evidence |
| --- | --- | --- | --- | --- | --- |
| Default SAM/BAM processing retains every alignment group until end of input | Memory scales with the whole retained input, and clustering cannot begin until reading finishes | Guarded streaming retains active groups and flushes them after a safe coordinate frontier | Same alignment/UMI grouping and configured algorithm rules for eligible input; documented deterministic ties and output ordering can differ from upstream | [`test/test-streaming.sh`](../test/test-streaming.sh) compares streaming and legacy records across supported algorithms, merge policies, and sequential data structures | Upstream comparison is reported in [`PERFORMANCE.md`](PERFORMANCE.md); the older on/off smoke test remains a regression signal |
| Coordinate sort order is trusted from the header | Incorrect metadata or excessive leading clipping can make an early flush unsafe | Runtime monotonicity check and a configurable positive-strand clipping window | `auto` retries legacy; forced `on` fails; neither path promotes incomplete output | Declared-unsorted, false-coordinate, clipping-boundary, and excessive-clipping fixtures in [`test/fixtures`](../test/fixtures) | Safety behavior is not itself a speed claim |
| Output is written directly while a run is in progress | A late failure can leave or replace a partial destination | Every advertised CLI route stages output beside the destination and promotes only a completed file | Existing destinations survive malformed input and processing failures; input and output cannot name the same file | Transactional-output cases in the hardening tests and `assert_fails_without_replacing` in [`test/test-streaming.sh`](../test/test-streaming.sh) | Not applicable |
| Default SAM UMI extraction uses regex matching, substring creation, case conversion, and boxed lookup | Per-record allocation and general parsing work are unnecessary for the default underscore convention | Direct suffix scan and direct `CharSequence`-slice encoding; a literal-separator parser remains for custom separators | Same accepted UMI alphabet and configured truncation behavior | [`TestOptimizedRegressions.java`](../src/test/TestOptimizedRegressions.java), [`TestBitSet.java`](../src/test/TestBitSet.java), and end-to-end parity tests | Aggregate upstream benchmark includes this path; no isolated parser result is claimed |
| `SAMRead` calculates average base quality at construction | Default mapping-quality selection does not use the result | Lazy, cached average-quality calculation | `avgqual` behavior is retained when requested | Merge-policy parity matrix in [`test/test-streaming.sh`](../test/test-streaming.sh) | Aggregate upstream benchmark includes this path; no isolated result is claimed |
| Directional clustering allocates and sorts the general structures for singleton groups | Sparse coordinate data can contain many groups that cannot require error-aware merging | Streaming caller bypass for single-UMI groups, lazy disabled tracking state, and a semantics-preserving public algorithm path | Direct algorithm calls retain data-structure state semantics; production streaming returns the same representative | Singleton algorithm and production-route regressions | Sparse-group workload in [`PERFORMANCE.md`](PERFORMANCE.md) exercises the combined path |
| Recursive graph traversal and frequency-only ordering depend on stack depth and hash iteration | Large components can exhaust the Java stack, while equal-frequency competition can vary with map order | Explicit traversal queues and deterministic UMI tie ordering | Cluster membership is stable for a fixed record multiset; `any` remains intentionally arbitrary | Determinism and deep-component hardening tests | This is primarily correctness and robustness work unless separately benchmarked |
| `NgramBKTree` creates object keys for every n-gram interval and performs repeated map probes | These operations cause repeated allocation and can substantially over-allocate the packed table relative to the reachable key universe | Packed 64-bit interval keys, an open-addressed map capped by the reachable n-gram key universe, and corrected pruning metadata; object-key fallback remains | `removeNear` membership and removal results must match the reference implementation | [`TestNgramBKTreeRegression.java`](../src/test/TestNgramBKTreeRegression.java) differentially compares against `Naive`, including packed-key boundaries | Dense-UMI workload in [`PERFORMANCE.md`](PERFORMANCE.md) exercises the combined path; no standalone data-structure speedup is claimed unless explicitly reported there |
| Paired output reopens the indexed BAM at reference transitions and assumes query support | Index parsing/file-open work repeats, while SAM and unindexed BAM cannot satisfy indexed queries | Incorporates persistent-reader PR [#32](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/pull/32), adds resource-safe closure, and uses indexed or sequential mate recovery as available | Selected reverse mates are recovered for indexed BAM, unindexed BAM, and SAM input | Paired indexed and sequential-recovery hardening cases | No paired performance result is currently claimed |
| The permissive CLI accepts unknown strategies and invalid numeric or UMI inputs until processing | Failures occur late, and short or zero-length UMIs can alias in the encoded key | Fail-fast option/range/compatibility validation, literal separators, and effective UMI-length enforcement | Invalid invocations fail before destination replacement; shorter UMIs never silently alias | CLI matrix and parser/short-UMI hardening tests | Not applicable |
| Runtime wrapper reserves a fixed large heap and build inputs are manually managed | Default operation is less portable and source-to-artifact correspondence is difficult to verify | JVM-default heap unless overridden, locked dependency checksums, clean Java 11-targeted builds, deterministic packaging, and a complete embedded build receipt | Users can supply `UMICOLLAPSE_JAVA_OPTS`; tagged source-and-binary releases carry exact dependencies, notices, checksums, SBOM, and receipt | [`scripts/check.sh`](../scripts/check.sh), [`scripts/verify-artifact.sh`](../scripts/verify-artifact.sh), reproducibility checks, and pinned CI/release workflows | Launcher behavior must be separated from code-isolated benchmark claims |

## Streaming design in more detail

Coordinate sorting uses aligned starts, while UMICollapse groups a
positive-strand read by its unclipped start. A future record can therefore join
an earlier group if it contains leading clipping. The streaming route delays
positive-strand flushes by the configured clipping allowance and validates each
record before applying that frontier.

Negative-strand groups use unclipped ends. Once the current alignment start has
advanced beyond such an end, a later coordinate-sorted record cannot join the
group. Reference changes provide a complete boundary for both strands.

The implementation deliberately favors a safe fallback over silently changing
deduplication membership. The cost is that an `auto` violation detected late in
the input restarts the workload on the legacy path.

## Packed n-gram design in more detail

Each nucleotide uses the existing three-bit UMICollapse encoding. A packed key
combines:

- the encoded interval sequence;
- the interval's low position;
- the interval's high position.

Packing is used only when the positions and encoded interval fit without
collision. Longer or wider intervals use the original `Interval` object
representation. Differential tests cover both sides of this routing boundary
and compare removal sequences, not merely initial lookups.

The packed map is an implementation detail behind the existing
`DataStructure` interface. Algorithms and command-line choices do not need a
separate packed-key mode.

## Claims policy

Public claims should follow these rules:

- describe output records as representative reads, not constructed consensus
  sequences;
- say coordinate-window-bounded working set, not constant or bounded memory;
- identify the exact upstream and dUMI commits used for a comparison;
- distinguish dUMI `off` from dUMI streaming so shared-core and streaming gains
  are not conflated;
- report record-equivalence checks with every benchmark;
- avoid production, platform-wide, or orders-of-magnitude claims unless a
  published workload directly supports them.
