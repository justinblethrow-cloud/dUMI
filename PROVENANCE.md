# Project provenance

dUMI is an independently maintained fork of
[UMICollapse](https://github.com/Daniel-Liu-c0deb0t/UMICollapse). It preserves
the upstream license, command-line model, clustering algorithms, and published
scientific attribution while adding implementation, streaming, validation, and
build changes.

## Canonical upstream baseline

The canonical upstream baseline is:

- repository: `Daniel-Liu-c0deb0t/UMICollapse`;
- commit:
  [`efeab35`](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/commit/efeab35f5d29dec1d496ade3f681eeb34d9c2057);
- commit subject: `Truncate UMIs if they vary in length`.

That commit remains the merge base between canonical upstream and this fork.

The label `v1.1.0` attached to `aeacd82` in some fork history is not a release
tag from the canonical upstream repository. It should not be used to describe
the canonical baseline.

## Incorporated upstream proposals

Immediately after the canonical baseline, this history incorporates commit
[`aeacd82`](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/pull/32/commits/aeacd8231cf8e77c03d03139ed6e65a4c2845015)
verbatim from Siddhartha Bagaria's upstream
[PR #32](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/pull/32). That change
keeps an indexed BAM reader open during paired mate recovery instead of
reopening the input at every reference transition.

The packed n-gram work addresses the same object-allocation opportunity
reported independently by `0jvh398j` in upstream
[PR #34](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/pull/34). The dUMI
implementation is carried in this repository's own commits and includes
fallback and differential-validation work beyond that proposal.

## Fork development lineage

The main implementation sequence after `aeacd82` is:

1. `d56aa49` — optimized UMI parsing, clustering, and n-gram indexing;
2. `92cccc2` — coordinate-sorted single-end streaming route;
3. `5b52d6a` — explicit streaming-mode control;
4. `56e63f2` — coordinate-order guard;
5. `f133fd8` — Java test-launcher repair;
6. `77ea792` — reproducible build, acceptance gate, and streaming hardening;
7. `d59df6d` — CI action-runtime maintenance;
8. `df169d8` — cross-platform and resource-safety hardening;
9. `2995329` — release-candidate correctness, resource, dependency,
   reproducibility, benchmark, and profiling hardening.

Git history is authoritative for exact file-level authorship and changes.

The consolidated proposal back to canonical upstream is
[UMICollapse PR #37](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/pull/37).
That proposal defaults streaming to `off` to preserve upstream behavior. This
fork intentionally defaults compatible workloads to guarded `auto` streaming.

## Scientific attribution

UMICollapse was introduced in:

> Daniel Liu. “Algorithms for efficiently collapsing reads with Unique
> Molecular Identifiers.” *PeerJ* 7:e8275 (2019).
> <https://doi.org/10.7717/peerj.8275>

See [`CITATION.cff`](CITATION.cff) for machine-readable citation metadata.

## License

The project remains under the upstream MIT License. See [`LICENSE`](LICENSE).
The retained copyright notice and permission terms apply to redistributed
copies and substantial portions of the software.

## Behavioral relationship to upstream

dUMI retains the upstream clustering choices and fallback execution paths.
Its guarded streaming route is an additional execution strategy for compatible
coordinate-sorted, single-end SAM/BAM input. Its output consists of selected
representative reads, not newly constructed base-level consensus sequences.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the two architectures,
[`docs/OPTIMIZATIONS.md`](docs/OPTIMIZATIONS.md) for change traceability, and
[`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) for compatibility boundaries.
