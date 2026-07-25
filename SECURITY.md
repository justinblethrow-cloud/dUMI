# Security policy

## Supported versions

| Version | Supported |
| --- | --- |
| `main` | Yes, as the current development line |
| `2.0.x` | Yes |
| Earlier dUMI or UMICollapse versions | No |

Security fixes normally land on `main` and are included in a supported
`2.0.x` release when applicable. Historical UMICollapse tags and development
snapshots are retained for provenance, not as separate support commitments.

## Reporting a vulnerability

Do not open a public issue containing exploit details, sensitive inputs,
credentials, or personally identifying sequencing data.

Use
[GitHub's private vulnerability report form](https://github.com/justinblethrow-cloud/dUMI/security/advisories/new)
to submit a report. Include:

- the affected commit and execution mode;
- a minimal reproduction that does not contain sensitive data;
- the expected and observed behavior;
- the potential confidentiality, integrity, or availability impact;
- any known workaround.

If you cannot access the private form, open a public issue containing no
technical details and ask the maintainer to establish a private channel. Do
not attach the reproduction until that channel exists.

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

Ordinary correctness bugs and performance regressions can use
[GitHub Issues](https://github.com/justinblethrow-cloud/dUMI/issues).
