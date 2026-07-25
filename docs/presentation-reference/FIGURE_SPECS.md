# Figure specifications

These specifications describe neutral, externally shareable figures. They are
reference material, not finished presentation artwork.

## General rules

- Use a plain light background and a restrained, color-blind-safe palette.
- Use color to distinguish upstream, shared core, streaming, and fallback
  behavior; do not use color decoratively.
- Label nodes directly and avoid a separate legend when practical.
- Keep code identifiers in monospace and explanatory text in a sans-serif
  face.
- Do not add logos, mascots, organization names, private paths, or internal
  workload names.
- Preserve editable text when a diagram is transferred into a presentation.

## Figure A: Canonical upstream architecture

Source:
[`upstream-architecture.mmd`](../diagrams/upstream-architecture.mmd).

Purpose: show how input modes converge on whole-input aggregation and the
pluggable clustering core.

Required labels:

- `Main: parse mode and strategies`;
- `SAM / BAM`;
- `FASTQ`;
- `Alignment key and UMI extraction`;
- `Whole-input group map and exact-UMI merge`;
- `Clustering algorithm`;
- `Neighbor-search data structure`;
- `Representative reads or tagged records`.

Caption:

> Canonical UMICollapse separates input grouping, exact-UMI representative
> selection, error-aware clustering, and output.

Do not imply that the merge policy creates a new consensus sequence.

## Figure B: Opportunity overlay

Base: Figure A.

Add four numbered callouts without changing the underlying flow:

1. whole-input alignment-group retention;
2. per-record parser and eager-quality work;
3. singleton general-path setup;
4. object-based n-gram interval keys.

Use the heading “Opportunities identified by code-path analysis.” Do not use
flame icons or label the opportunities as profiler discoveries. The retained
post-change allocation profile may appear as a separate validation note, but
it is not a before/after profiler result.

## Figure C: Shared-core changes

Format: three horizontal rows with upstream at left, change in the center, and
compatibility fallback at right.

Rows:

1. regex/substrings/eager quality → direct default parse/lazy quality → literal
   custom-separator parser;
2. eager per-group map and clustering setup → lazy singleton accumulator and
   bypass → general path for multi-UMI groups;
3. object interval keys → packed keys/open addressing → object-key fallback.

Caption:

> Common cases avoid general work while preserving a path for unsupported key
> shapes and configurations.

## Figure D: Streaming safety frontier

Format: a horizontal coordinate axis with:

- completed groups behind the flush frontier;
- active groups inside the retained window;
- the current aligned start;
- a future-record region;
- a positive-strand leading-clipping allowance.

Add a side branch:

```text
contract violation
  ├─ auto: discard streaming attempt and retry legacy
  └─ on: fail without replacing destination
```

Caption:

> The flush frontier advances only after runtime order and clipping checks make
> an earlier group safe to release.

Call the result a coordinate-window-bounded working set, not bounded memory.

## Figure E: Validation matrix

Rows:

- optimized parser and singleton semantics;
- `NgramBKTree` differential behavior;
- streaming/legacy record equivalence;
- single-end SAM/BAM and paired indexed-BAM, unindexed-BAM, and SAM behavior;
- sort metadata and destination preservation;
- post-change allocation sentinels and positive controls;
- Java and operating-system matrix.

Columns:

- reference or oracle;
- representative cases;
- expected invariant;
- evidence file.

Use checkmarks only for gates actually recorded in
[`VALIDATION.md`](../../VALIDATION.md).

## Figure F: Performance comparison

Source data: [`benchmark-summary.csv`](benchmark-summary.csv), interpreted in
[`PERFORMANCE.md`](../PERFORMANCE.md) and backed by the
[clean evidence package](../benchmarks/2026-07-25/README.md).

Label the entire figure “Fixed-seed synthetic scaling measurements.” Use three
aligned small multiples for the four single-end workloads:

- matched raw speedup versus canonical upstream;
- matched raw-plus-ready speedup versus canonical upstream;
- matched raw peak-RSS reduction versus canonical upstream.

Within each panel, use the same candidate order:

1. dUMI streaming `off`;
2. dUMI streaming `on`;
3. dUMI default `auto`.

Use a reference line at 1.0x for speedup panels and 0% for the RSS panel. Show
the matched seven-repetition range with whiskers. The raw-plus-ready panel must
state that streaming cells include `samtools sort` plus indexing, while
coordinate-sorted upstream and `off` cells require indexing only.

Add a compact paired attribution inset with two workload groups:

- 10 references / 100 records: show the PR #32 intermediate and dUMI values,
  and label the dUMI result a tiny fixed-cost regression check;
- 1,000 references / 10,000 records: show that the large gain is already
  present at `aeacd82` before the later dUMI changes.

State that both paired fixtures contain unique pairs and therefore measure
traversal/reference-transition scaling and preservation, not paired duplicate
collapse or representative selection.

Put canonical `efeab35`, dUMI `2995329`, PR #32 intermediate `aeacd82`, the
common runtime, and seven matched repetitions in the source note. Do not mix
launcher-default results with the code-isolated equal-JVM comparison.

## Figure G: Post-change allocation diagnostic

Source data:
[`allocation-aggregate.json`](../benchmarks/2026-07-25/profile/allocation-aggregate.json),
[`profile-correctness.json`](../benchmarks/2026-07-25/profile/profile-correctness.json),
and
[`profile-receipt.json`](../benchmarks/2026-07-25/profile/profile-receipt.json).

Format: a compact sentinel table beside a horizontal ranking of the leading
sampled allocation sites.

Required context:

- three Java 21 runs of the one-million-record synthetic sparse workload;
- forced streaming, clean frozen commit `2995329`;
- exact record and reference-dictionary checks passed in every run;
- all three expected-absent singleton-setup sentinels were zero;
- both positive controls were sampled in every run.

Label bar values “JFR sample-weight share,” not allocated bytes or retained
heap. Group HTSJDK decoding, byte copying, CIGAR/list construction, and
BAM-record creation distinctly from dUMI UMI, group, alignment, and read
objects.

Caption:

> Post-change sampling observed no singleton-setup sentinel events on this
> synthetic workload; remaining sampled pressure was dominated by decoding and
> representation work.

Add a footnote that zero sampled weight does not prove a site can never
allocate. Do not show the undistributed development profile, calculate a
before/after reduction, or imply that profiling discovered the original
opportunities.

## Figure H: Resulting architecture

Source:
[`resulting-architecture.mmd`](../diagrams/resulting-architecture.mmd).

Purpose: show the full routing decision, shared optimized core, safe promotion,
and retained fallback.

Caption:

> dUMI adds guarded streaming for eligible SAM/BAM input while retaining
> compatible legacy routes for the broader interface.

The final figure must show both failure branches:

- `auto` retry to legacy;
- forced `on` failure without destination replacement.

Show the final output node as “transactionally staged output” because the outer
CLI transaction protects streaming and non-streaming routes.
