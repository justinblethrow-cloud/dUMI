#!/usr/bin/env python3
"""Fingerprint and compare SAM/BAM alignment-record multisets.

Record order and implementation-specific header lines are intentionally
excluded.  Each input is decoded by samtools, record lines are sorted with the
C locale, and the exact sorted byte stream is counted and hashed.  Duplicate
records therefore remain significant.  Ordered @SQ and @RG lines are
fingerprinted separately; @HD sort-order and @PG provenance are reported or
ignored as appropriate, but are not semantic-equality inputs.

A second fingerprint represents the alignment-group output-count multiset. It
deliberately excludes read names and representative-record details while
retaining repeated groups. It can distinguish a representative-record change
from a per-group output-count change, but cannot establish UMI identity or
equal cluster partitions.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
from dataclasses import dataclass
import filecmp
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


REPORT_FIELDS = (
    "output_file",
    "quickcheck",
    "quickcheck_status",
    "output_records",
    "semantic_sha256",
    "sort_order",
    "reference_sequences",
    "reference_dictionary_sha256",
    "read_groups",
    "read_group_dictionary_sha256",
    "expected_reference_sequences",
    "expected_reference_dictionary_sha256",
    "expected_read_groups",
    "expected_read_group_dictionary_sha256",
    "alignment_group_fingerprint_version",
    "alignment_group_mode",
    "alignment_group_output_records",
    "alignment_group_records_excluded_unmapped",
    "alignment_group_records_excluded_second_of_pair",
    "alignment_group_output_count_sha256",
    "alignment_group_output_count_reused_from_exact_reference",
    "reference_file",
    "reference_file_sha256",
    "reference_canonical_sha256",
    "reference_canonical_sha256_verified",
    "reference_cache_receipt_verified",
    "reference_cache_receipt_sha256",
    "reference_alignment_group_output_records",
    "reference_alignment_group_records_excluded_unmapped",
    "reference_alignment_group_records_excluded_second_of_pair",
    "reference_alignment_group_output_count_sha256",
    "record_equivalent",
    "reference_dictionary_equivalent",
    "read_group_dictionary_equivalent",
    "alignment_group_output_count_equivalent",
)

ALIGNMENT_GROUP_MODES = ("single-end", "paired")
ALIGNMENT_GROUP_FINGERPRINT_VERSION = (
    "dumi-umicollapse-alignment-group-output-count-v1"
)
CACHE_RECEIPT_SCHEMA = "dumi-semantic-canonical-cache-v1"
CACHE_RECEIPT_FIELDS = frozenset(
    (
        "schema",
        "source_file_sha256",
        "canonical_records",
        "canonical_sha256",
        "reference_sequences",
        "reference_dictionary_sha256",
        "read_groups",
        "read_group_dictionary_sha256",
        "alignment_group_fingerprint_version",
        "alignment_group_mode",
        "alignment_group_output_records",
        "alignment_group_records_excluded_unmapped",
        "alignment_group_records_excluded_second_of_pair",
        "alignment_group_output_count_sha256",
    )
)
PUBLIC_SORT_ORDERS = frozenset(("coordinate", "queryname", "unsorted", "unknown"))
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
CIGAR_PATTERN = re.compile(rb"(\d+)([MIDNSHP=X])")
REFERENCE_CONSUMING_CIGAR_OPERATORS = frozenset(b"MDN=X")
CLIPPING_CIGAR_OPERATORS = frozenset(b"SH")


class CheckError(RuntimeError):
    """An input could not be validated or canonicalized."""


@dataclass(frozen=True)
class HeaderMetadata:
    """Header fields that are meaningful across implementations."""

    sort_order: str
    reference_sequences: int
    reference_dictionary_sha256: str
    reference_dictionary: bytes
    read_groups: int
    read_group_dictionary_sha256: str
    read_group_dictionary: bytes


@dataclass(frozen=True)
class AlignmentGroupFingerprint:
    """Count-only multiset fingerprint of represented alignment groups.

    This can establish how many output records represent each UMICollapse
    alignment group.  It intentionally cannot establish UMI identity, cluster
    partition membership, or representative provenance.
    """

    mode: str
    records: int
    excluded_unmapped: int
    excluded_second_of_pair: int
    sha256: str


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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise CheckError(f"could not hash {path}: {error}") from error
    return digest.hexdigest()


def paths_collide(first: Path, second: Path) -> bool:
    """Compare path identity and, when possible, underlying inode identity."""

    if first.absolute() == second.absolute():
        return True
    try:
        if first.exists() and second.exists():
            return os.path.samefile(first, second)
    except OSError as error:
        raise CheckError(
            f"could not compare output path identities: {error}"
        ) from error
    return False


def reject_destination_collisions(
    destinations: list[tuple[str, Path]],
    protected: list[tuple[str, Path]],
) -> None:
    for destination_label, destination in destinations:
        if destination.exists() and not destination.is_file():
            raise CheckError(
                f"{destination_label} is not a regular file: {destination}"
            )
        for protected_label, protected_path in protected:
            if paths_collide(destination, protected_path):
                raise CheckError(
                    f"{destination_label} must not overwrite or alias "
                    f"{protected_label}"
                )
    for index, (first_label, first) in enumerate(destinations):
        for second_label, second in destinations[index + 1 :]:
            if paths_collide(first, second):
                raise CheckError(
                    f"{first_label} and {second_label} must be distinct files"
                )


@contextmanager
def atomic_private_destination(
    destination: Path,
    protected: list[tuple[str, Path]],
):
    """Yield a mode-0600 sibling and atomically install it on success."""

    parent = destination.parent
    if not parent.is_dir():
        raise CheckError(f"output parent directory does not exist: {parent}")
    reject_destination_collisions(
        [("output destination", destination)],
        protected,
    )
    temporary: Path | None = None
    try:
        descriptor, temporary_string = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=parent,
        )
        temporary = Path(temporary_string)
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        yield temporary
        os.chmod(temporary, 0o600)
        reject_destination_collisions(
            [("output destination", destination)],
            protected,
        )
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
        temporary = None
    except CheckError:
        raise
    except OSError as error:
        raise CheckError(
            f"could not atomically write {destination}: {error}"
        ) from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def atomic_write_text(
    destination: Path,
    content: str,
    protected: list[tuple[str, Path]],
) -> None:
    with atomic_private_destination(destination, protected) as temporary:
        try:
            with temporary.open("w", encoding="utf-8", newline="") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as error:
            raise CheckError(
                f"could not write temporary output for {destination}: {error}"
            ) from error


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


def header_metadata(path: Path, samtools: str) -> HeaderMetadata:
    result = run_checked(
        [samtools, "view", "-H", str(path)],
        f"header read for {path}",
    )
    sort_order = "unknown"
    reference_digest = hashlib.sha256()
    reference_dictionary = bytearray()
    reference_sequences = 0
    read_group_digest = hashlib.sha256()
    read_group_dictionary = bytearray()
    read_groups = 0
    for raw_line in result.stdout.splitlines():
        if raw_line.startswith(b"@HD\t"):
            for raw_field in raw_line.split(b"\t")[1:]:
                if raw_field.startswith(b"SO:"):
                    value = raw_field[3:].decode("utf-8", errors="replace").strip()
                    normalized = value.lower()
                    sort_order = (
                        normalized
                        if normalized in PUBLIC_SORT_ORDERS
                        else "other"
                    )
                    break
        elif raw_line.startswith(b"@SQ\t"):
            reference_digest.update(raw_line)
            reference_digest.update(b"\n")
            reference_dictionary.extend(raw_line)
            reference_dictionary.extend(b"\n")
            reference_sequences += 1
        elif raw_line.startswith(b"@RG\t"):
            read_group_digest.update(raw_line)
            read_group_digest.update(b"\n")
            read_group_dictionary.extend(raw_line)
            read_group_dictionary.extend(b"\n")
            read_groups += 1
    return HeaderMetadata(
        sort_order=sort_order,
        reference_sequences=reference_sequences,
        reference_dictionary_sha256=reference_digest.hexdigest(),
        reference_dictionary=bytes(reference_dictionary),
        read_groups=read_groups,
        read_group_dictionary_sha256=read_group_digest.hexdigest(),
        read_group_dictionary=bytes(read_group_dictionary),
    )


def canonicalize(
    path: Path,
    destination: Path,
    samtools: str,
    sort_command: str,
    temporary_directory: Path,
    protected: list[tuple[str, Path]],
) -> tuple[int, str]:
    view_stderr = temporary_directory / f"{destination.name}.samtools.stderr"
    sort_stderr = temporary_directory / f"{destination.name}.sort.stderr"
    environment = deterministic_environment()
    environment["TMPDIR"] = str(temporary_directory)

    with atomic_private_destination(destination, protected) as temporary_output:
        try:
            with (
                temporary_output.open("wb") as sorted_output,
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
            detail = view_stderr.read_text(
                encoding="utf-8", errors="replace"
            ).strip()
            raise CheckError(
                f"samtools view failed for {path}: "
                f"{detail or f'exit code {view_returncode}'}"
            )
        if sort_returncode != 0:
            detail = sort_stderr.read_text(
                encoding="utf-8", errors="replace"
            ).strip()
            raise CheckError(
                f"record sort failed for {path}: "
                f"{detail or f'exit code {sort_returncode}'}"
            )

        digest = hashlib.sha256()
        records = 0
        final_byte = b""
        try:
            with temporary_output.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                    records += chunk.count(b"\n")
                    final_byte = chunk[-1:]
            if temporary_output.stat().st_size and final_byte != b"\n":
                raise CheckError(
                    f"canonical record stream for {path} lacks a final newline"
                )
        except OSError as error:
            raise CheckError(
                f"could not fingerprint canonical records for {path}: {error}"
            ) from error
    return records, digest.hexdigest()


def fingerprint_canonical(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    records = 0
    final_byte = b""
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                records += chunk.count(b"\n")
                final_byte = chunk[-1:]
        if path.stat().st_size and final_byte != b"\n":
            raise CheckError(
                f"canonical record stream for {path} lacks a final newline"
            )
    except CheckError:
        raise
    except OSError as error:
        raise CheckError(
            f"could not fingerprint canonical stream {path}: {error}"
        ) from error
    return records, digest.hexdigest()


def exact_files_equal(first: Path, second: Path) -> bool:
    try:
        return filecmp.cmp(first, second, shallow=False)
    except OSError as error:
        raise CheckError(f"could not compare canonical streams: {error}") from error


def parse_cigar(cigar: bytes, record_number: int, source: Path) -> list[tuple[int, int]]:
    """Parse a SAM CIGAR without retaining or echoing input-derived contents."""

    if not cigar or cigar == b"*":
        raise CheckError(
            f"mapped record {record_number} in {source} does not have a CIGAR"
        )
    elements: list[tuple[int, int]] = []
    consumed = 0
    for match in CIGAR_PATTERN.finditer(cigar):
        if match.start() != consumed:
            raise CheckError(
                f"mapped record {record_number} in {source} has an invalid CIGAR"
            )
        length = int(match.group(1))
        if length <= 0:
            raise CheckError(
                f"mapped record {record_number} in {source} has an invalid CIGAR"
            )
        elements.append((length, match.group(2)[0]))
        consumed = match.end()
    if not elements or consumed != len(cigar):
        raise CheckError(
            f"mapped record {record_number} in {source} has an invalid CIGAR"
        )
    return elements


def unclipped_coordinate(
    *,
    position: int,
    cigar: bytes,
    reverse: bool,
    record_number: int,
    source: Path,
) -> int:
    """Reproduce HTSJDK's unclipped 5-prime coordinate calculation."""

    elements = parse_cigar(cigar, record_number, source)
    if position <= 0:
        raise CheckError(
            f"mapped record {record_number} in {source} has an invalid position"
        )

    if reverse:
        reference_length = sum(
            length
            for length, operator in elements
            if operator in REFERENCE_CONSUMING_CIGAR_OPERATORS
        )
        if reference_length <= 0:
            raise CheckError(
                f"mapped record {record_number} in {source} has no "
                "reference-consuming CIGAR operation"
            )
        coordinate = position + reference_length - 1
        for length, operator in reversed(elements):
            if operator not in CLIPPING_CIGAR_OPERATORS:
                break
            coordinate += length
        return coordinate

    coordinate = position
    for length, operator in elements:
        if operator not in CLIPPING_CIGAR_OPERATORS:
            break
        coordinate -= length
    return coordinate


def alignment_group_key_digest(
    reference_name: bytes,
    reverse: bool,
    coordinate: int,
    template_length: int | None,
) -> bytes:
    """Hash an unambiguous group key so temporary sort input is content-free."""

    digest = hashlib.sha256()
    components = (
        ALIGNMENT_GROUP_FINGERPRINT_VERSION.encode("ascii"),
        reference_name,
        b"reverse" if reverse else b"forward",
        str(coordinate).encode("ascii"),
        b"" if template_length is None else str(template_length).encode("ascii"),
    )
    for component in components:
        digest.update(len(component).to_bytes(8, byteorder="big", signed=False))
        digest.update(component)
    return digest.hexdigest().encode("ascii")


def sort_key_file(
    unsorted_path: Path,
    sorted_path: Path,
    sort_command: str,
    temporary_directory: Path,
) -> None:
    """Sort fixed-width hashed keys without exposing record contents."""

    sort_stderr = temporary_directory / f"{sorted_path.name}.sort.stderr"
    environment = deterministic_environment()
    environment["TMPDIR"] = str(temporary_directory)
    try:
        with (
            unsorted_path.open("rb") as unsorted_input,
            sorted_path.open("wb") as sorted_output,
            sort_stderr.open("wb") as sort_error,
        ):
            result = subprocess.run(
                [sort_command],
                stdin=unsorted_input,
                stdout=sorted_output,
                stderr=sort_error,
                env=environment,
                check=False,
            )
    except OSError as error:
        raise CheckError(
            f"alignment-group sort could not start: {error}"
        ) from error
    if result.returncode != 0:
        detail = sort_stderr.read_text(encoding="utf-8", errors="replace").strip()
        raise CheckError(
            "alignment-group sort failed: "
            f"{detail or f'exit code {result.returncode}'}"
        )


def alignment_group_fingerprint(
    canonical_records: Path,
    mode: str,
    sort_command: str,
    temporary_directory: Path,
    label: str,
) -> AlignmentGroupFingerprint:
    """Fingerprint output counts per UMICollapse alignment group.

    The digest represents the alignment-group output-count multiset; it is not
    a UMI or cluster-partition equivalence proof.
    """

    unsorted_keys = temporary_directory / f"{label}.alignment-groups.unsorted"
    sorted_keys = temporary_directory / f"{label}.alignment-groups.sorted"
    records = 0
    excluded_unmapped = 0
    excluded_second = 0

    try:
        with (
            canonical_records.open("rb") as source,
            unsorted_keys.open("wb") as destination,
        ):
            for record_number, raw_line in enumerate(source, start=1):
                if not raw_line.endswith(b"\n"):
                    raise CheckError(
                        f"canonical record {record_number} in "
                        f"{canonical_records} lacks a final newline"
                    )
                fields = raw_line[:-1].split(b"\t", 11)
                if len(fields) < 11:
                    raise CheckError(
                        f"canonical record {record_number} in "
                        f"{canonical_records} is not a valid SAM record"
                    )
                try:
                    flag = int(fields[1])
                    position = int(fields[3])
                    template_length = int(fields[8])
                except ValueError as error:
                    raise CheckError(
                        f"canonical record {record_number} in "
                        f"{canonical_records} has a non-integer core field"
                    ) from error

                if mode == "paired" and flag & 0x1 and flag & 0x80:
                    excluded_second += 1
                    continue
                if flag & 0x4:
                    excluded_unmapped += 1
                    continue
                if fields[2] == b"*":
                    raise CheckError(
                        f"mapped record {record_number} in "
                        f"{canonical_records} does not name a reference"
                    )

                reverse = bool(flag & 0x10)
                coordinate = unclipped_coordinate(
                    position=position,
                    cigar=fields[5],
                    reverse=reverse,
                    record_number=record_number,
                    source=canonical_records,
                )
                key_digest = alignment_group_key_digest(
                    fields[2],
                    reverse,
                    coordinate,
                    template_length if mode == "paired" else None,
                )
                destination.write(key_digest)
                destination.write(b"\n")
                records += 1
    except OSError as error:
        raise CheckError(
            f"could not fingerprint alignment groups for "
            f"{canonical_records}: {error}"
        ) from error

    sort_key_file(
        unsorted_keys,
        sorted_keys,
        sort_command,
        temporary_directory,
    )
    sorted_records, digest = fingerprint_canonical(sorted_keys)
    if sorted_records != records:
        raise CheckError(
            "alignment-group count changed during canonicalization for "
            f"{canonical_records}"
        )
    return AlignmentGroupFingerprint(
        mode=mode,
        records=records,
        excluded_unmapped=excluded_unmapped,
        excluded_second_of_pair=excluded_second,
        sha256=digest,
    )


def write_report(
    rows: list[dict[str, object]],
    report_path: str | None,
    include_header: bool,
    report_format: str,
    protected: list[tuple[str, Path]],
) -> None:
    stream = io.StringIO(newline="")
    if report_format == "json":
        payload: object = rows[0] if len(rows) == 1 else rows
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
    else:
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
    content = stream.getvalue()
    if report_path is None or report_path == "-":
        try:
            sys.stdout.write(content)
            sys.stdout.flush()
        except OSError as error:
            raise CheckError(f"could not write semantic report: {error}") from error
        return
    destination = Path(report_path).absolute()
    try:
        destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    except OSError as error:
        raise CheckError(
            f"could not create report directory {destination.parent}: {error}"
        ) from error
    atomic_write_text(destination, content, protected)


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
        "--reference-canonical",
        help=(
            "precomputed byte-sorted SAM record stream for --reference; "
            "requires --reference-canonical-receipt"
        ),
    )
    parser.add_argument(
        "--reference-canonical-receipt",
        help=(
            "trusted cache receipt binding --reference to its canonical "
            "record stream, ordered @SQ/@RG dictionaries, and cached "
            "alignment-group output-count fingerprint"
        ),
    )
    parser.add_argument(
        "--reference-canonical-sha256",
        help=(
            "expected SHA-256 of --reference-canonical; when supplied, fail "
            "closed if the private cache has changed"
        ),
    )
    parser.add_argument(
        "--canonical-output",
        help=(
            "retain the byte-sorted SAM record stream for one inspected output; "
            "requires --canonical-receipt-output"
        ),
    )
    parser.add_argument(
        "--canonical-receipt-output",
        help=(
            "write a path-free provenance receipt for --canonical-output; "
            "the receipt contains only hashes, counts, modes, and schema labels"
        ),
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
        "--alignment-group-mode",
        choices=ALIGNMENT_GROUP_MODES,
        default="single-end",
        help=(
            "count output records per UMICollapse alignment group: single-end "
            "uses (reference,strand,unclipped-coordinate); paired additionally "
            "uses TLEN and ignores second-of-pair records. This is not UMI or "
            "cluster-partition equivalence (default: single-end)"
        ),
    )
    parser.add_argument(
        "--molecule-mode",
        dest="alignment_group_mode",
        choices=ALIGNMENT_GROUP_MODES,
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
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


def cache_receipt_payload(
    *,
    source_file_sha256: str,
    canonical_records: int,
    canonical_sha256: str,
    header: HeaderMetadata,
    alignment_groups: AlignmentGroupFingerprint,
) -> dict[str, object]:
    return {
        "schema": CACHE_RECEIPT_SCHEMA,
        "source_file_sha256": source_file_sha256,
        "canonical_records": canonical_records,
        "canonical_sha256": canonical_sha256,
        "reference_sequences": header.reference_sequences,
        "reference_dictionary_sha256": header.reference_dictionary_sha256,
        "read_groups": header.read_groups,
        "read_group_dictionary_sha256": (
            header.read_group_dictionary_sha256
        ),
        "alignment_group_fingerprint_version": (
            ALIGNMENT_GROUP_FINGERPRINT_VERSION
        ),
        "alignment_group_mode": alignment_groups.mode,
        "alignment_group_output_records": alignment_groups.records,
        "alignment_group_records_excluded_unmapped": (
            alignment_groups.excluded_unmapped
        ),
        "alignment_group_records_excluded_second_of_pair": (
            alignment_groups.excluded_second_of_pair
        ),
        "alignment_group_output_count_sha256": alignment_groups.sha256,
    }


def write_cache_receipt(
    destination: Path,
    payload: dict[str, object],
    protected: list[tuple[str, Path]],
) -> None:
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    atomic_write_text(destination, content, protected)


def load_cache_receipt(path: Path) -> dict[str, object]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CheckError(f"could not read canonical cache receipt: {error}") from error
    if not isinstance(payload, dict):
        raise CheckError("canonical cache receipt must be one JSON object")
    fields = set(payload)
    if fields != CACHE_RECEIPT_FIELDS:
        raise CheckError(
            "canonical cache receipt schema fields differ "
            f"(missing_count={len(CACHE_RECEIPT_FIELDS - fields)}, "
            f"extra_count={len(fields - CACHE_RECEIPT_FIELDS)})"
        )
    if payload.get("schema") != CACHE_RECEIPT_SCHEMA:
        raise CheckError("canonical cache receipt schema is unsupported")
    if (
        payload.get("alignment_group_fingerprint_version")
        != ALIGNMENT_GROUP_FINGERPRINT_VERSION
    ):
        raise CheckError(
            "canonical cache receipt alignment-group fingerprint version "
            "is unsupported"
        )
    if payload.get("alignment_group_mode") not in ALIGNMENT_GROUP_MODES:
        raise CheckError(
            "canonical cache receipt alignment-group mode is invalid"
        )
    sha_fields = (
        "source_file_sha256",
        "canonical_sha256",
        "reference_dictionary_sha256",
        "read_group_dictionary_sha256",
        "alignment_group_output_count_sha256",
    )
    for field in sha_fields:
        value = payload.get(field)
        if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
            raise CheckError(
                f"canonical cache receipt field {field} is not a SHA-256"
            )
        payload[field] = value.lower()
    count_fields = (
        "canonical_records",
        "reference_sequences",
        "read_groups",
        "alignment_group_output_records",
        "alignment_group_records_excluded_unmapped",
        "alignment_group_records_excluded_second_of_pair",
    )
    for field in count_fields:
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CheckError(
                f"canonical cache receipt field {field} is not a "
                "nonnegative integer"
            )
    classified_records = (
        int(payload["alignment_group_output_records"])
        + int(payload["alignment_group_records_excluded_unmapped"])
        + int(
            payload[
                "alignment_group_records_excluded_second_of_pair"
            ]
        )
    )
    if classified_records != int(payload["canonical_records"]):
        raise CheckError(
            "canonical cache receipt record classifications do not sum "
            "to canonical_records"
        )
    return payload


def fingerprint_from_cache_receipt(
    payload: dict[str, object],
) -> AlignmentGroupFingerprint:
    return AlignmentGroupFingerprint(
        mode=str(payload["alignment_group_mode"]),
        records=int(payload["alignment_group_output_records"]),
        excluded_unmapped=int(
            payload["alignment_group_records_excluded_unmapped"]
        ),
        excluded_second_of_pair=int(
            payload[
                "alignment_group_records_excluded_second_of_pair"
            ]
        ),
        sha256=str(payload["alignment_group_output_count_sha256"]),
    )


def main() -> int:
    arguments = parse_arguments()
    previous_umask = os.umask(0o077)
    try:
        samtools = command_path(arguments.samtools)
        sort_command = command_path(arguments.sort_command)
        temporary_parent = None
        if arguments.tmpdir:
            temporary_parent = Path(arguments.tmpdir).absolute()
            if not temporary_parent.is_dir():
                raise CheckError(
                    f"temporary directory does not exist: {temporary_parent}"
                )

        outputs = [validate_input(path) for path in arguments.outputs]
        reference = validate_input(arguments.reference) if arguments.reference else None
        reference_canonical_input = (
            validate_input(arguments.reference_canonical)
            if arguments.reference_canonical
            else None
        )
        reference_cache_receipt_input = (
            validate_input(arguments.reference_canonical_receipt)
            if arguments.reference_canonical_receipt
            else None
        )
        if reference_canonical_input is not None and reference is None:
            raise CheckError("--reference-canonical requires --reference")
        if (
            reference_canonical_input is None
            and reference_cache_receipt_input is not None
        ):
            raise CheckError(
                "--reference-canonical-receipt requires "
                "--reference-canonical"
            )
        if (
            reference_canonical_input is not None
            and reference_cache_receipt_input is None
        ):
            raise CheckError(
                "--reference-canonical requires "
                "--reference-canonical-receipt"
            )
        if (
            arguments.reference_canonical_sha256 is not None
            and reference_canonical_input is None
        ):
            raise CheckError(
                "--reference-canonical-sha256 requires --reference-canonical"
            )
        expected_reference_canonical_sha256: str | None = None
        if arguments.reference_canonical_sha256 is not None:
            if not SHA256_PATTERN.fullmatch(
                arguments.reference_canonical_sha256
            ):
                raise CheckError(
                    "--reference-canonical-sha256 must be exactly 64 hexadecimal characters"
                )
            expected_reference_canonical_sha256 = (
                arguments.reference_canonical_sha256.lower()
            )
        if bool(arguments.canonical_output) != bool(
            arguments.canonical_receipt_output
        ):
            raise CheckError(
                "--canonical-output and --canonical-receipt-output "
                "must be supplied together"
            )
        if arguments.canonical_output and len(outputs) != 1:
            raise CheckError(
                "--canonical-output requires exactly one inspected output"
            )
        retained_canonical = (
            Path(arguments.canonical_output).absolute()
            if arguments.canonical_output
            else None
        )
        retained_cache_receipt = (
            Path(arguments.canonical_receipt_output).absolute()
            if arguments.canonical_receipt_output
            else None
        )
        report_destination = (
            Path(arguments.report).absolute()
            if arguments.report and arguments.report != "-"
            else None
        )

        protected: list[tuple[str, Path]] = [
            ("inspected input", path) for path in outputs
        ]
        if reference is not None:
            protected.append(("reference input", reference))
        if reference_canonical_input is not None:
            protected.append(
                ("reference canonical cache", reference_canonical_input)
            )
        if reference_cache_receipt_input is not None:
            protected.append(
                (
                    "reference canonical cache receipt",
                    reference_cache_receipt_input,
                )
            )
        destinations: list[tuple[str, Path]] = []
        if retained_canonical is not None:
            destinations.append(("canonical-output", retained_canonical))
        if retained_cache_receipt is not None:
            destinations.append(
                ("canonical-receipt-output", retained_cache_receipt)
            )
        if report_destination is not None:
            destinations.append(("report", report_destination))
        reject_destination_collisions(destinations, protected)
        for label, destination in destinations:
            if label != "report" and not destination.parent.is_dir():
                raise CheckError(
                    f"{label} parent directory does not exist: "
                    f"{destination.parent}"
                )

        def write_protected(destination: Path) -> list[tuple[str, Path]]:
            return protected + [
                (f"other {label}", path)
                for label, path in destinations
                if destination.absolute() != path.absolute()
            ]

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
            reference_header: HeaderMetadata | None = None
            reference_alignment_groups: AlignmentGroupFingerprint | None = None
            reference_file_digest: str | None = None
            reference_canonical_verified: bool | None = None
            reference_cache_receipt_verified: bool | None = None
            reference_cache_receipt_digest: str | None = None
            if reference is not None:
                quickcheck(reference, samtools)
                reference_header = header_metadata(reference, samtools)
                reference_file_digest = sha256_file(reference)
                if reference_canonical_input is not None:
                    assert reference_cache_receipt_input is not None
                    receipt_digest_before = sha256_file(
                        reference_cache_receipt_input
                    )
                    cache_receipt = load_cache_receipt(
                        reference_cache_receipt_input
                    )
                    reference_cache_receipt_digest = sha256_file(
                        reference_cache_receipt_input
                    )
                    if (
                        reference_cache_receipt_digest
                        != receipt_digest_before
                    ):
                        raise CheckError(
                            "reference cache receipt changed while it was "
                            "being read"
                        )
                    if (
                        cache_receipt["source_file_sha256"]
                        != reference_file_digest
                    ):
                        raise CheckError(
                            "reference file SHA-256 does not match its "
                            "canonical cache receipt"
                        )
                    if (
                        cache_receipt["alignment_group_mode"]
                        != arguments.alignment_group_mode
                    ):
                        raise CheckError(
                            "reference cache receipt alignment-group mode "
                            "does not match --alignment-group-mode"
                        )
                    header_receipt_values = (
                        (
                            "reference_sequences",
                            reference_header.reference_sequences,
                        ),
                        (
                            "reference_dictionary_sha256",
                            reference_header.reference_dictionary_sha256,
                        ),
                        ("read_groups", reference_header.read_groups),
                        (
                            "read_group_dictionary_sha256",
                            reference_header.read_group_dictionary_sha256,
                        ),
                    )
                    for field, observed in header_receipt_values:
                        if cache_receipt[field] != observed:
                            raise CheckError(
                                "reference header does not match canonical "
                                f"cache receipt field {field}"
                            )
                    reference_canonical = reference_canonical_input
                    reference_records, reference_digest = fingerprint_canonical(
                        reference_canonical
                    )
                    if (
                        cache_receipt["canonical_records"]
                        != reference_records
                        or cache_receipt["canonical_sha256"]
                        != reference_digest
                    ):
                        raise CheckError(
                            "reference canonical stream does not match its "
                            "cache receipt"
                        )
                    if expected_reference_canonical_sha256 is not None:
                        if (
                            reference_digest
                            != expected_reference_canonical_sha256
                        ):
                            raise CheckError(
                                "reference canonical SHA-256 does not match "
                                "--reference-canonical-sha256"
                            )
                    reference_canonical_verified = True
                    reference_cache_receipt_verified = True
                    reference_alignment_groups = (
                        fingerprint_from_cache_receipt(cache_receipt)
                    )
                else:
                    reference_canonical = directory / "reference.records.sorted"
                    reference_records, reference_digest = canonicalize(
                        reference,
                        reference_canonical,
                        samtools,
                        sort_command,
                        directory,
                        protected,
                    )
                    reference_alignment_groups = alignment_group_fingerprint(
                        reference_canonical,
                        arguments.alignment_group_mode,
                        sort_command,
                        directory,
                        "reference",
                    )

            for index, output in enumerate(outputs):
                cache_source_digest_before = (
                    sha256_file(output)
                    if retained_cache_receipt is not None
                    else None
                )
                quickcheck(output, samtools)
                canonical = (
                    retained_canonical
                    if retained_canonical is not None
                    else directory / f"candidate-{index}.records.sorted"
                )
                assert canonical is not None
                records, digest = canonicalize(
                    output,
                    canonical,
                    samtools,
                    sort_command,
                    directory,
                    (
                        write_protected(canonical)
                        if retained_canonical is not None
                        else protected
                    ),
                )
                output_header = header_metadata(output, samtools)
                equivalent: bool | None = None
                if reference_canonical is not None:
                    equivalent = (
                        records == reference_records
                        and digest == reference_digest
                        and exact_files_equal(canonical, reference_canonical)
                    )
                alignment_group_reused = bool(equivalent)
                if alignment_group_reused:
                    assert reference_alignment_groups is not None
                    output_alignment_groups = reference_alignment_groups
                else:
                    output_alignment_groups = alignment_group_fingerprint(
                        canonical,
                        arguments.alignment_group_mode,
                        sort_command,
                        directory,
                        f"candidate-{index}",
                )
                output_file_digest: str | None = None
                if retained_cache_receipt is not None:
                    stable_records, stable_digest = fingerprint_canonical(
                        canonical
                    )
                    if stable_records != records or stable_digest != digest:
                        raise CheckError(
                            "canonical output changed while its cache receipt "
                            "was being created"
                        )
                    output_file_digest = sha256_file(output)
                    if output_file_digest != cache_source_digest_before:
                        raise CheckError(
                            "canonical cache source changed while its receipt "
                            "was being created"
                        )
                    receipt_payload = cache_receipt_payload(
                        source_file_sha256=output_file_digest,
                        canonical_records=records,
                        canonical_sha256=digest,
                        header=output_header,
                        alignment_groups=output_alignment_groups,
                    )
                    write_cache_receipt(
                        retained_cache_receipt,
                        receipt_payload,
                        write_protected(retained_cache_receipt),
                    )
                dictionary_equivalent: bool | None
                read_group_equivalent: bool | None
                alignment_group_equivalent: bool | None
                if reference_canonical is None:
                    dictionary_equivalent = None
                    read_group_equivalent = None
                    alignment_group_equivalent = None
                else:
                    assert reference_header is not None
                    assert reference_alignment_groups is not None
                    assert equivalent is not None
                    dictionary_equivalent = (
                        output_header.reference_dictionary
                        == reference_header.reference_dictionary
                        and output_header.reference_dictionary_sha256
                        == reference_header.reference_dictionary_sha256
                    )
                    read_group_equivalent = (
                        output_header.read_group_dictionary
                        == reference_header.read_group_dictionary
                        and output_header.read_group_dictionary_sha256
                        == reference_header.read_group_dictionary_sha256
                    )
                    alignment_group_equivalent = (
                        output_alignment_groups.records
                        == reference_alignment_groups.records
                        and output_alignment_groups.sha256
                        == reference_alignment_groups.sha256
                    )
                    mismatch = (
                        mismatch
                        or not equivalent
                        or not dictionary_equivalent
                        or not read_group_equivalent
                        or not alignment_group_equivalent
                    )
                rows.append(
                    {
                        "output_file": str(output),
                        "quickcheck": True,
                        "quickcheck_status": "pass",
                        "output_records": records,
                        "semantic_sha256": digest,
                        "sort_order": output_header.sort_order,
                        "reference_sequences": (
                            output_header.reference_sequences
                        ),
                        "reference_dictionary_sha256": (
                            output_header.reference_dictionary_sha256
                        ),
                        "read_groups": output_header.read_groups,
                        "read_group_dictionary_sha256": (
                            output_header.read_group_dictionary_sha256
                        ),
                        "expected_reference_sequences": (
                            reference_header.reference_sequences
                            if reference_header is not None
                            else None
                        ),
                        "expected_reference_dictionary_sha256": (
                            reference_header.reference_dictionary_sha256
                            if reference_header is not None
                            else ""
                        ),
                        "expected_read_groups": (
                            reference_header.read_groups
                            if reference_header is not None
                            else None
                        ),
                        "expected_read_group_dictionary_sha256": (
                            reference_header.read_group_dictionary_sha256
                            if reference_header is not None
                            else ""
                        ),
                        "alignment_group_fingerprint_version": (
                            ALIGNMENT_GROUP_FINGERPRINT_VERSION
                        ),
                        "alignment_group_mode": (
                            output_alignment_groups.mode
                        ),
                        "alignment_group_output_records": (
                            output_alignment_groups.records
                        ),
                        "alignment_group_records_excluded_unmapped": (
                            output_alignment_groups.excluded_unmapped
                        ),
                        "alignment_group_records_excluded_second_of_pair": (
                            output_alignment_groups.excluded_second_of_pair
                        ),
                        "alignment_group_output_count_sha256": (
                            output_alignment_groups.sha256
                        ),
                        "alignment_group_output_count_reused_from_exact_reference": (
                            alignment_group_reused
                        ),
                        "reference_file": str(reference) if reference else "",
                        "reference_file_sha256": (
                            reference_file_digest
                            if reference_file_digest is not None
                            else ""
                        ),
                        "reference_canonical_sha256": (
                            reference_digest if reference is not None else ""
                        ),
                        "reference_canonical_sha256_verified": (
                            reference_canonical_verified
                        ),
                        "reference_cache_receipt_verified": (
                            reference_cache_receipt_verified
                        ),
                        "reference_cache_receipt_sha256": (
                            reference_cache_receipt_digest
                            if reference_cache_receipt_digest is not None
                            else ""
                        ),
                        "reference_alignment_group_output_records": (
                            reference_alignment_groups.records
                            if reference_alignment_groups is not None
                            else None
                        ),
                        "reference_alignment_group_records_excluded_unmapped": (
                            reference_alignment_groups.excluded_unmapped
                            if reference_alignment_groups is not None
                            else None
                        ),
                        "reference_alignment_group_records_excluded_second_of_pair": (
                            reference_alignment_groups.excluded_second_of_pair
                            if reference_alignment_groups is not None
                            else None
                        ),
                        "reference_alignment_group_output_count_sha256": (
                            reference_alignment_groups.sha256
                            if reference_alignment_groups is not None
                            else ""
                        ),
                        "record_equivalent": equivalent,
                        "reference_dictionary_equivalent": dictionary_equivalent,
                        "read_group_dictionary_equivalent": (
                            read_group_equivalent
                        ),
                        "alignment_group_output_count_equivalent": (
                            alignment_group_equivalent
                        ),
                    }
                )

            if reference_canonical_input is not None:
                stable_records, stable_digest = fingerprint_canonical(
                    reference_canonical_input
                )
                if (
                    stable_records != reference_records
                    or stable_digest != reference_digest
                ):
                    raise CheckError(
                        "reference canonical changed during semantic checking"
                    )
                assert reference is not None
                assert reference_file_digest is not None
                if sha256_file(reference) != reference_file_digest:
                    raise CheckError(
                        "reference input changed during semantic checking"
                    )
                assert reference_cache_receipt_input is not None
                assert reference_cache_receipt_digest is not None
                if (
                    sha256_file(reference_cache_receipt_input)
                    != reference_cache_receipt_digest
                ):
                    raise CheckError(
                        "reference cache receipt changed during semantic "
                        "checking"
                    )

        write_report(
            rows,
            arguments.report,
            not arguments.no_header,
            arguments.format,
            (
                protected
                + [
                    (f"other {label}", path)
                    for label, path in destinations
                    if report_destination is None
                    or report_destination.absolute() != path.absolute()
                ]
            ),
        )
        return 1 if mismatch else 0
    except CheckError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"error: filesystem operation failed: {error}", file=sys.stderr)
        return 2
    finally:
        os.umask(previous_umask)


if __name__ == "__main__":
    sys.exit(main())
