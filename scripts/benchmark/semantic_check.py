#!/usr/bin/env python3
"""Fingerprint and compare SAM/BAM alignment-record multisets.

Record order and non-reference header lines are intentionally excluded.  Each
input is decoded by samtools, record lines are sorted with the C locale, and
the exact sorted byte stream is counted and hashed.  Duplicate records
therefore remain significant.  Ordered @SQ lines are fingerprinted separately.
"""

from __future__ import annotations

import argparse
import csv
import filecmp
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import TextIO


REPORT_FIELDS = (
    "output_file",
    "quickcheck",
    "quickcheck_status",
    "output_records",
    "semantic_sha256",
    "sort_order",
    "reference_sequences",
    "reference_dictionary_sha256",
    "reference_file",
    "record_equivalent",
    "reference_dictionary_equivalent",
)


class CheckError(RuntimeError):
    """An input could not be validated or canonicalized."""


def command_path(command: str) -> str:
    resolved = shutil.which(command)
    if resolved is None:
        raise CheckError(f"required command not found: {command}")
    return resolved


def deterministic_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    environment["LANG"] = "C"
    environment["TZ"] = "UTC"
    return environment


def run_checked(command: list[str], description: str) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=deterministic_environment(),
            check=False,
        )
    except OSError as error:
        raise CheckError(f"{description} could not start: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        if not detail:
            detail = f"exit code {result.returncode}"
        raise CheckError(f"{description} failed: {detail}")
    return result


def quickcheck(path: Path, samtools: str) -> None:
    run_checked([samtools, "quickcheck", "-v", str(path)], f"quickcheck for {path}")


def header_metadata(path: Path, samtools: str) -> tuple[str, int, str]:
    result = run_checked(
        [samtools, "view", "-H", str(path)],
        f"header read for {path}",
    )
    sort_order = "unknown"
    reference_digest = hashlib.sha256()
    reference_sequences = 0
    for raw_line in result.stdout.splitlines():
        if raw_line.startswith(b"@HD\t"):
            for raw_field in raw_line.split(b"\t")[1:]:
                if raw_field.startswith(b"SO:"):
                    value = raw_field[3:].decode("utf-8", errors="replace").strip()
                    sort_order = value or "unknown"
                    break
        elif raw_line.startswith(b"@SQ\t"):
            reference_digest.update(raw_line)
            reference_digest.update(b"\n")
            reference_sequences += 1
    return sort_order, reference_sequences, reference_digest.hexdigest()


def canonicalize(
    path: Path,
    destination: Path,
    samtools: str,
    sort_command: str,
    temporary_directory: Path,
) -> tuple[int, str]:
    view_stderr = temporary_directory / f"{destination.name}.samtools.stderr"
    sort_stderr = temporary_directory / f"{destination.name}.sort.stderr"
    environment = deterministic_environment()
    environment["TMPDIR"] = str(temporary_directory)

    try:
        with (
            destination.open("wb") as sorted_output,
            view_stderr.open("wb") as view_error,
            sort_stderr.open("wb") as sort_error,
        ):
            view = subprocess.Popen(
                [samtools, "view", str(path)],
                stdout=subprocess.PIPE,
                stderr=view_error,
                env=environment,
            )
            assert view.stdout is not None
            sorter = subprocess.Popen(
                [sort_command],
                stdin=view.stdout,
                stdout=sorted_output,
                stderr=sort_error,
                env=environment,
            )
            view.stdout.close()
            sort_returncode = sorter.wait()
            view_returncode = view.wait()
    except OSError as error:
        raise CheckError(f"could not canonicalize {path}: {error}") from error

    if view_returncode != 0:
        detail = view_stderr.read_text(encoding="utf-8", errors="replace").strip()
        raise CheckError(
            f"samtools view failed for {path}: "
            f"{detail or f'exit code {view_returncode}'}"
        )
    if sort_returncode != 0:
        detail = sort_stderr.read_text(encoding="utf-8", errors="replace").strip()
        raise CheckError(
            f"record sort failed for {path}: "
            f"{detail or f'exit code {sort_returncode}'}"
        )

    digest = hashlib.sha256()
    records = 0
    final_byte = b""
    with destination.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            records += chunk.count(b"\n")
            final_byte = chunk[-1:]
    if destination.stat().st_size and final_byte != b"\n":
        raise CheckError(f"canonical record stream for {path} lacks a final newline")
    return records, digest.hexdigest()


def output_stream(path: str | None) -> tuple[TextIO, bool]:
    if path is None or path == "-":
        return sys.stdout, False
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination.open("w", encoding="utf-8", newline=""), True


def write_report(
    rows: list[dict[str, object]],
    report_path: str | None,
    include_header: bool,
    report_format: str,
) -> None:
    stream, should_close = output_stream(report_path)
    try:
        if report_format == "json":
            payload: object = rows[0] if len(rows) == 1 else rows
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            return
        writer = csv.DictWriter(
            stream,
            fieldnames=REPORT_FIELDS,
            delimiter="\t",
            lineterminator="\n",
        )
        if include_header:
            writer.writeheader()
        for row in rows:
            serialized = {
                key: (
                    value.lower()
                    if isinstance(value, str) and key == "record_equivalent"
                    else str(value).lower()
                    if isinstance(value, bool)
                    else "not_checked"
                    if value is None
                    else value
                )
                for key, value in row.items()
            }
            writer.writerow(serialized)
    finally:
        if should_close:
            stream.close()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compute order-independent exact SAM-record fingerprints and "
            "optionally compare one or more outputs with a reference."
        )
    )
    parser.add_argument("outputs", nargs="+", help="SAM/BAM outputs to inspect")
    parser.add_argument(
        "--reference",
        help="SAM/BAM reference whose record multiset each output must match",
    )
    parser.add_argument(
        "--report",
        metavar="PATH",
        help="write the report to PATH instead of standard output",
    )
    parser.add_argument(
        "--format",
        choices=("json", "tsv"),
        default="json",
        help="report encoding (default: json)",
    )
    parser.add_argument(
        "--no-header",
        action="store_true",
        help="omit the TSV header (useful when appending one result row)",
    )
    parser.add_argument(
        "--samtools",
        default="samtools",
        help="samtools executable (default: samtools)",
    )
    parser.add_argument(
        "--sort-command",
        default="sort",
        help="bytewise line-sort executable (default: sort)",
    )
    parser.add_argument(
        "--tmpdir",
        "--temp-dir",
        dest="tmpdir",
        help="parent directory for external-sort temporary files",
    )
    return parser.parse_args()


def validate_input(path_string: str) -> Path:
    path = Path(path_string)
    if not path.is_file():
        raise CheckError(f"input is not a regular file: {path}")
    return path.resolve()


def main() -> int:
    arguments = parse_arguments()
    try:
        samtools = command_path(arguments.samtools)
        sort_command = command_path(arguments.sort_command)
        temporary_parent = None
        if arguments.tmpdir:
            temporary_parent = Path(arguments.tmpdir)
            if not temporary_parent.is_dir():
                raise CheckError(
                    f"temporary directory does not exist: {temporary_parent}"
                )

        outputs = [validate_input(path) for path in arguments.outputs]
        reference = validate_input(arguments.reference) if arguments.reference else None
        if arguments.report and arguments.report != "-":
            report = Path(arguments.report).resolve()
            protected_inputs = set(outputs)
            if reference is not None:
                protected_inputs.add(reference)
            if report in protected_inputs:
                raise CheckError("report path must not overwrite an inspected input")
        rows: list[dict[str, object]] = []
        mismatch = False

        with tempfile.TemporaryDirectory(
            prefix="dumi-semantic-check.",
            dir=str(temporary_parent) if temporary_parent else None,
        ) as directory_string:
            directory = Path(directory_string)
            reference_canonical: Path | None = None
            reference_records: int | None = None
            reference_digest: str | None = None
            reference_dictionary_digest: str | None = None
            if reference is not None:
                quickcheck(reference, samtools)
                (
                    _,
                    _,
                    reference_dictionary_digest,
                ) = header_metadata(reference, samtools)
                reference_canonical = directory / "reference.records.sorted"
                reference_records, reference_digest = canonicalize(
                    reference,
                    reference_canonical,
                    samtools,
                    sort_command,
                    directory,
                )

            for index, output in enumerate(outputs):
                quickcheck(output, samtools)
                canonical = directory / f"candidate-{index}.records.sorted"
                records, digest = canonicalize(
                    output,
                    canonical,
                    samtools,
                    sort_command,
                    directory,
                )
                (
                    sort_order,
                    reference_sequences,
                    reference_dictionary_sha256,
                ) = header_metadata(output, samtools)
                equivalent: bool | None
                dictionary_equivalent: bool | None
                if reference_canonical is None:
                    equivalent = None
                    dictionary_equivalent = None
                else:
                    equivalent_value = (
                        records == reference_records
                        and digest == reference_digest
                        and filecmp.cmp(
                            canonical,
                            reference_canonical,
                            shallow=False,
                        )
                    )
                    equivalent = equivalent_value
                    dictionary_equivalent = (
                        reference_dictionary_sha256 == reference_dictionary_digest
                    )
                    mismatch = (
                        mismatch
                        or not equivalent_value
                        or not dictionary_equivalent
                    )
                rows.append(
                    {
                        "output_file": str(output),
                        "quickcheck": True,
                        "quickcheck_status": "pass",
                        "output_records": records,
                        "semantic_sha256": digest,
                        "sort_order": sort_order,
                        "reference_sequences": reference_sequences,
                        "reference_dictionary_sha256": (
                            reference_dictionary_sha256
                        ),
                        "reference_file": str(reference) if reference else "",
                        "record_equivalent": equivalent,
                        "reference_dictionary_equivalent": dictionary_equivalent,
                    }
                )

        write_report(
            rows,
            arguments.report,
            not arguments.no_header,
            arguments.format,
        )
        return 1 if mismatch else 0
    except CheckError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
