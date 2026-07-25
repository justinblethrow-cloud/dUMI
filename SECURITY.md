# Security policy

## Supported versions

Until dUMI publishes a versioned release, the current default branch is the
only line considered for best-effort security corrections. Historical
UMICollapse tags and development snapshots are retained for provenance and are
not separate dUMI support commitments.

## Reporting a vulnerability

Do not open a public issue containing exploit details, sensitive inputs,
credentials, or personally identifying sequencing data.

If GitHub displays a **Report a vulnerability** option on the repository's
Security page, use it to submit a private report. Include:

- the affected commit and execution mode;
- a minimal reproduction that does not contain sensitive data;
- the expected and observed behavior;
- the potential confidentiality, integrity, or availability impact;
- any known workaround.

If private vulnerability reporting is unavailable, open a public issue with no
technical details asking the maintainer to establish a private channel. Do not
attach the reproduction until that channel exists.

Reports will be acknowledged and assessed on a best-effort basis. A fix,
release, or disclosure timeline depends on reproducibility, severity, and
maintainer availability.

## Dependency reports

This repository checksum-locks dependencies for reproducibility. A checksum
lock does not establish that a dependency is current or vulnerability-free.
Reports affecting HTSJDK, snappy-java, the JDK, or GitHub Actions should name
the relevant advisory and explain whether dUMI's actual use is affected.

## Scope

Security-relevant examples include:

- unintended overwrite or partial replacement of an output file;
- unsafe archive, path, or temporary-file handling;
- malformed input causing uncontrolled resource consumption;
- dependency or build-artifact substitution;
- exposure of sensitive read data in logs or fixtures.

Ordinary correctness bugs and performance regressions can use the regular
issue tracker once it is enabled.
