# Presentation reference storyboard

This is a content handoff for a short, neutral presentation. It is not a slide
deck and contains no organization-specific branding.

## Content budget

- Maximum: eight content slides, excluding a title or discussion slide.
- Title: at most 10 words.
- Takeaway sentence: at most 25 words.
- Supporting text: at most three bullets, each at most 12 words.
- Visuals: one principal diagram, table, or chart per slide.
- Performance claims: use only accepted values from
  [`benchmark-summary.csv`](benchmark-summary.csv).
- Allocation observations: use only the retained
  [post-change profile](../benchmarks/2026-07-25/profile/allocation-aggregate.json).
- Source note: one compact line linking to the canonical repository evidence.

## 1. Why revisit UMICollapse?

**Takeaway:** UMICollapse already provides efficient UMI clustering; the fork
targets avoidable retention and per-record work around that core.

Supporting points:

- Preserve the established clustering model and command-line choices.
- Improve the common coordinate-sorted single-end path.
- Require output equivalence and explicit safety boundaries.

Visual: a simple “preserve / improve / validate” three-part frame.

Evidence: [`PROVENANCE.md`](../../PROVENANCE.md) and
[`ARCHITECTURE.md`](../ARCHITECTURE.md).

## 2. Upstream architecture

**Takeaway:** The default SAM/BAM route reads the whole input into alignment
groups before clustering and writing representatives.

Supporting points:

- Exact UMI duplicates merge inside each alignment group.
- Directional plus `NgramBKTree` is the default clustering core.
- Optional tagging and two-pass routes reread the input.

Visual: [`upstream-architecture.mmd`](../diagrams/upstream-architecture.mmd).

Evidence: canonical upstream `efeab35` and the upstream section of
[`ARCHITECTURE.md`](../ARCHITECTURE.md).

## 3. Opportunities in the execution path

**Takeaway:** Code-path analysis identified one architectural opportunity and
several repeated allocation or eager-work opportunities.

Supporting points:

- Whole-input retention delays release of completed alignment groups.
- Default UMI parsing and quality calculation do unnecessary work.
- Singleton clustering and n-gram object keys add repeated overhead.

Visual: upstream architecture with four numbered opportunity callouts.

Evidence: [`OPTIMIZATIONS.md`](../OPTIMIZATIONS.md). Do not label these as
profiler findings.

## 4. Optimized shared clustering core

**Takeaway:** The shared core reduces avoidable parsing, allocation, lookup,
and singleton setup while retaining general fallbacks.

Supporting points:

- Direct default-UMI scan and encoding; lazy average quality.
- Lazy single-UMI accumulation and streaming singleton bypass.
- Post-change samples were dominated by decoding and representation.

Visual: a three-row “upstream mechanism → fork mechanism → fallback” table.

Evidence: parser, directional, and `NgramBKTree` rows in
[`OPTIMIZATIONS.md`](../OPTIMIZATIONS.md), plus the retained
[post-change allocation profile](../benchmarks/2026-07-25/profile/allocation-aggregate.json).
The profile is a diagnostic check of the resulting singleton path; it is not a
before/after profiler result and did not identify the original opportunities.

## 5. Guarded streaming

**Takeaway:** Eligible coordinate-sorted input releases completed groups behind
a validated safety frontier.

Supporting points:

- Active group map plus coordinate-ordered flush queue.
- Runtime order and clipping checks protect membership.
- Transactional output enables fallback without partial replacement.

Visual: a coordinate timeline showing active, safe-to-flush, and future
groups; include the `auto → legacy` fallback arrow.

Evidence: streaming sections of [`ARCHITECTURE.md`](../ARCHITECTURE.md) and
[`LIMITATIONS.md`](../LIMITATIONS.md).

## 6. Validation strategy

**Takeaway:** Validation compares optimized behavior with reference paths and
tests both success and failure semantics.

Supporting points:

- Differential structures and streaming/legacy record equivalence.
- Failure safety, CLI, paired, and deterministic-tie regressions.
- Three post-change allocation runs with positive controls.

Visual: a compact matrix with rows “core,” “streaming,” “failure safety,”
“allocation diagnostic,” and “platform.”

Evidence: [`VALIDATION.md`](../../VALIDATION.md),
[`TestNgramBKTreeRegression.java`](../../src/test/TestNgramBKTreeRegression.java),
[`test-streaming.sh`](../../test/test-streaming.sh), and the
[profile evidence](../benchmarks/2026-07-25/profile/profile-correctness.json).

## 7. Measured comparison with upstream

**Takeaway:** Across four synthetic single-end workloads, `auto` delivered
1.30x–2.75x raw speedups; downstream-ready gains were 1.27x–1.96x.

Supporting points:

- Peak RSS fell 15.83%–83.46% versus canonical upstream.
- Sorting and indexing reduce every streaming speedup.
- Tiny paired regression retained; large gain traces to PR #32.

Visual: one compound figure with matched raw speedup, raw-plus-ready speedup,
and peak-RSS reduction for the four single-end workloads. Add a small paired
inset showing both the 10-reference regression check and the 1,000-reference
gain beside the PR #32 intermediate. Label every workload as fixed-seed
synthetic; disclose that the unique-pair fixtures test traversal,
reference-transition scaling, and preservation rather than duplicate collapse
or representative selection. Do not use a dual axis.

Evidence: [`PERFORMANCE.md`](../PERFORMANCE.md) and
[`benchmark-summary.csv`](benchmark-summary.csv), backed by the
[clean evidence package](../benchmarks/2026-07-25/README.md). The source note
must name canonical `efeab35`, dUMI `2995329`, seven matched repetitions, and
`aeacd82` from unmerged PR #32 for the paired attribution.

## 8. Resulting architecture and applicability

**Takeaway:** dUMI adds a guarded streaming route and optimized shared core
while retaining legacy routes for the full interface.

Supporting points:

- Compatible single-end SAM/BAM defaults to guarded `auto`.
- Incompatible modes continue through the legacy architecture.
- Reordered streaming output is explicitly declared unsorted.

Visual: [`resulting-architecture.mmd`](../diagrams/resulting-architecture.mmd).

Evidence: resulting-architecture section of
[`ARCHITECTURE.md`](../ARCHITECTURE.md) and
[`LIMITATIONS.md`](../LIMITATIONS.md).

## Language guardrails

Use:

- representative read;
- coordinate-window-bounded working set;
- measured on the specified workload;
- fixed-seed synthetic scaling measurement;
- raw-plus-ready includes downstream sorting and indexing;
- post-change allocation sampling observed;
- code-path analysis identified;
- canonical upstream `efeab35`.

Avoid:

- consensus read;
- constant or bounded memory;
- production-wide performance;
- orders-of-magnitude improvement over UMICollapse;
- profiling discovered the original opportunities;
- a zero allocation sample proves a site never executes;
- paired performance gain without PR #32 attribution;
- upstream v1.1.0 for `aeacd82`.
