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
flame icons or a “profiling” label because no retained profile supports that
claim.

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
- SAM/BAM and indexed-BAM, unindexed-BAM, and SAM paired behavior;
- sort metadata and destination preservation;
- Java and operating-system matrix.

Columns:

- reference or oracle;
- representative cases;
- expected invariant;
- evidence file.

Use checkmarks only for gates actually recorded in
[`VALIDATION.md`](../../VALIDATION.md).

## Figure F: Performance comparison

Source data:
[`benchmark-summary.csv`](benchmark-summary.csv).

Use two small multiples:

- median elapsed seconds;
- median maximum RSS in KiB or MiB.

Within each panel, use the same candidate order:

1. canonical upstream;
2. dUMI streaming `off`;
3. dUMI streaming `on` or default `auto` when eligibility is demonstrated.

Show the measured spread using whiskers when repeated measurements are
available. Put exact commit identifiers and repetition count in a source note.
Do not mix default upstream fixed-heap launcher results with code-isolated
equal-JVM results in the same comparison.

## Figure G: Resulting architecture

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
