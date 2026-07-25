# Contributing

Contributions that improve correctness, performance, portability,
documentation, or reproducibility are welcome.

## Before opening a change

- Search existing issues and pull requests for related work.
- Use the repository's issue tracker for reproducible bugs and design
  proposals when it is available.
- Follow [`SECURITY.md`](SECURITY.md) for suspected vulnerabilities; do not
  disclose sensitive security details in a public issue.
- Keep public examples and fixtures free of private, identifying, or
  organization-specific data.

For a behavior change, describe the compatibility contract before
implementation. For a performance change, identify the workload and the
correctness oracle that will be used to evaluate it.

## Development setup

Requirements:

- a JDK 11 or newer;
- Bash;
- `curl` and `unzip`;
- `sha256sum` on Linux or `shasum` on macOS.

Fetch or verify dependencies:

```bash
./scripts/bootstrap-dependencies.sh
```

Run the complete acceptance gate:

```bash
./scripts/check.sh
```

The gate rebuilds production and test classes, verifies the packaged artifact,
runs data-structure tests, and exercises SAM/BAM compatibility and failure
paths.

## Pull-request expectations

A focused pull request should include:

- a concise problem statement;
- the affected modes and command-line options;
- tests that fail before and pass after the change;
- evidence that existing output semantics remain intact;
- documentation updates for new behavior or limitations.

Please avoid mixing unrelated refactoring, behavior changes, generated
artifacts, and benchmark claims in one change.

### Correctness changes

Where practical, compare an optimized implementation with a simpler reference
implementation. Include edge cases around data-structure routing, input order,
output replacement, and file-header semantics.

Describe retained output as a representative read unless the implementation
actually constructs a base-level consensus.

### Performance changes

Performance results must record:

- exact baseline and candidate commits;
- input generator or public input provenance and checksum;
- complete commands and JVM options;
- JDK, operating system, CPU, and measurement tool;
- repetition count and summary statistic;
- output-equivalence result.

Run baselines and candidates under equivalent runtime settings. Keep raw
machine-readable results with the benchmark report. A synthetic result is a
claim about that workload, not a production-wide guarantee.

### Streaming changes

Streaming changes must preserve the safety properties documented in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md):

- no unsafe early group flush;
- automatic fallback when the `auto` contract is violated;
- failure without destination replacement in forced `on` mode;
- accurate output sort metadata.

Use “coordinate-window-bounded working set,” not “constant memory” or
“bounded memory.”

## Style and scope

Match the existing Java and shell style unless a separate formatting change is
being proposed. Keep the Java 11 bytecode target unless a compatibility change
has been discussed explicitly.

Do not add runtime dependencies without documenting their purpose, license,
checksum, and effect on supported platforms.

## Licensing and attribution

By contributing, you agree that your contribution may be distributed under
the repository's MIT License. Preserve upstream attribution and identify
third-party code or ideas in the pull-request description and relevant source
comments.
