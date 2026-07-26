#!/usr/bin/env python3
"""Compare UMICollapse ``--tag`` cluster partitions without exposing records.

The two inputs must be tagged SAM/BAM outputs produced from the same
pre-deduplication input.  Numeric MI identifiers, RX roots, representative
selection, record order, QNAME prefixes, and @PG provenance are deliberately
excluded.  The comparison retains the exact UMI-frequency membership of every
cluster in every UMICollapse alignment group.

Large inputs are handled as two bounded-memory external-sort passes:

1. eligible records become ``alignment-key, MI, UMI`` rows;
2. each ``alignment-key, MI`` cluster becomes a sorted UMI-frequency
   signature, MI is discarded, and those signatures are sorted again.

Only aggregate counts and cryptographic digests enter the JSON receipt.
Temporary files contain input-derived UMI data and are therefore created in a
private 0700 workspace with 0600 files.  The CLI removes them after normal
completion, handled errors, SIGTERM, and SIGHUP; SIGKILL cannot be cleaned by
any process.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from typing import BinaryIO, Iterator


SCHEMA = "dumi-cluster-partition-check-v1"
PARTITION_VERSION = "umicollapse-tag-alignment-cluster-umi-frequency-v1"
MODES = ("single-end", "paired")
INT32_MIN = -(1 << 31)
INT32_MAX = (1 << 31) - 1
UINT32_MAX = (1 << 32) - 1
SIGNED_OFFSET = 1 << 31
FLAG_PAIRED = 0x1
FLAG_UNMAPPED = 0x4
FLAG_MATE_UNMAPPED = 0x8
FLAG_REVERSE = 0x10
FLAG_SECOND = 0x80
UMI_BASES = frozenset(b"ATCGNatcgn")
CIGAR_PATTERN = re.compile(rb"(\d+)([MIDNSHP=X])")
REFERENCE_CONSUMING = frozenset(b"MDN=X")
CLIPPING = frozenset(b"SH")
MI_PATTERN = re.compile(rb"[0-9]+")
SORT_BUFFER_PATTERN = re.compile(r"[1-9][0-9]*(?:[KMGTP]%?|%)?", re.IGNORECASE)


class PartitionCheckError(RuntimeError):
    """An input, tool, or private-workspace invariant failed."""


class PartitionSignalInterrupt(KeyboardInterrupt):
    """A termination signal converted into a cleanup-safe stack unwind."""

    def __init__(self, signal_number: int):
        super().__init__(signal_number)
        self.signal_number = signal_number


def _raise_signal_interrupt(signal_number: int, _frame: object) -> None:
    """Disable repeated termination signals, then unwind ordinary control flow."""
    for signal_name in ("SIGHUP", "SIGTERM"):
        candidate = getattr(signal, signal_name, None)
        if candidate is not None:
            signal.signal(candidate, signal.SIG_IGN)
    raise PartitionSignalInterrupt(signal_number)


def install_termination_signal_handlers() -> None:
    """Convert scheduler termination signals into cleanup-safe exceptions."""
    for signal_name in ("SIGHUP", "SIGTERM"):
        signal_number = getattr(signal, signal_name, None)
        if signal_number is not None:
            signal.signal(signal_number, _raise_signal_interrupt)


@dataclass(frozen=True)
class InputIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class HeaderMetadata:
    reference_records: tuple[bytes, ...]
    read_group_records: tuple[bytes, ...]
    reference_index: dict[bytes, int]
    reference_dictionary_sha256: str
    read_group_dictionary_sha256: str


@dataclass
class RecordMetrics:
    input_records: int = 0
    eligible_records: int = 0
    excluded_unmapped: int = 0
    excluded_second_of_pair: int = 0
    excluded_unpaired: int = 0
    excluded_mate_unmapped: int = 0
    excluded_chimeric: int = 0


@dataclass(frozen=True)
class PartitionMetrics:
    input_records: int
    eligible_records: int
    excluded_unmapped: int
    excluded_second_of_pair: int
    excluded_unpaired: int
    excluded_mate_unmapped: int
    excluded_chimeric: int
    alignment_groups: int
    clusters: int
    umi_memberships: int
    max_umi_memberships_per_cluster: int
    record_key_bytes: int
    canonical_partition_bytes: int
    partition_cluster_multiset_sha256: str
    reference_sequences: int
    reference_dictionary_sha256: str
    read_groups: int
    read_group_dictionary_sha256: str


def deterministic_environment(temporary_directory: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    environment["LANG"] = "C"
    environment["TZ"] = "UTC"
    environment["TMPDIR"] = os.fspath(temporary_directory)
    return environment


def command_path(command: str, label: str) -> str:
    resolved = shutil.which(command)
    if resolved is None:
        raise PartitionCheckError(f"required {label} command was not found")
    return resolved


def resolve_gnu_sort(command: str | None) -> str:
    """Resolve and verify GNU coreutils sort, preferring Homebrew gsort."""
    candidates = [command] if command else ["gsort", "sort"]
    for candidate in candidates:
        if not candidate:
            continue
        resolved = shutil.which(candidate)
        if resolved is None:
            continue
        try:
            completed = subprocess.run(
                [resolved, "--version"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={
                    **os.environ,
                    "LANG": "C",
                    "LC_ALL": "C",
                    "TZ": "UTC",
                },
            )
        except OSError:
            continue
        version = (completed.stdout or "") + (completed.stderr or "")
        if completed.returncode == 0 and "GNU coreutils" in version:
            return resolved
    raise PartitionCheckError(
        "GNU sort is required ('sort' on Linux or Homebrew 'gsort'); "
        "pass --sort-command explicitly"
    )


def input_identity(path: Path, label: str) -> InputIdentity:
    try:
        metadata = path.stat()
    except OSError as error:
        raise PartitionCheckError(f"{label} input could not be inspected") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise PartitionCheckError(f"{label} input is not a regular file")
    return InputIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        size=metadata.st_size,
        mtime_ns=metadata.st_mtime_ns,
    )


def validate_unchanged(path: Path, expected: InputIdentity, label: str) -> None:
    if input_identity(path, label) != expected:
        raise PartitionCheckError(f"{label} input changed during comparison")


@contextmanager
def private_workspace(parent: Path | None = None) -> Iterator[Path]:
    if parent is not None:
        try:
            parent_metadata = parent.stat()
        except OSError as error:
            raise PartitionCheckError(
                "temporary parent directory could not be inspected"
            ) from error
        if not stat.S_ISDIR(parent_metadata.st_mode):
            raise PartitionCheckError("temporary parent is not a directory")
    try:
        workspace = Path(
            tempfile.mkdtemp(
                prefix=".dumi-cluster-partition-",
                dir=os.fspath(parent) if parent is not None else None,
            )
        )
        workspace.chmod(0o700)
    except OSError as error:
        raise PartitionCheckError("private temporary workspace could not be created") from error
    try:
        yield workspace
    finally:
        try:
            shutil.rmtree(workspace)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise PartitionCheckError(
                "private temporary workspace could not be removed"
            ) from error


def secure_binary_output(path: Path) -> BinaryIO:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        os.fchmod(descriptor, 0o600)
    except OSError as error:
        raise PartitionCheckError("private temporary file could not be created") from error
    return os.fdopen(descriptor, "wb")


def canonical_header_record(raw_line: bytes, record_type: bytes) -> tuple[bytes, dict[bytes, bytes]]:
    fields = raw_line.rstrip(b"\r\n").split(b"\t")
    if not fields or fields[0] != record_type:
        raise PartitionCheckError("SAM header contains a malformed record")
    tags: dict[bytes, bytes] = {}
    for field in fields[1:]:
        if len(field) < 4 or field[2:3] != b":":
            raise PartitionCheckError("SAM header contains a malformed tag")
        tag = field[:2]
        if tag in tags:
            raise PartitionCheckError("SAM header contains a duplicate tag")
        tags[tag] = field[3:]
    canonical = record_type + b"\t" + b"\t".join(
        tag + b":" + tags[tag] for tag in sorted(tags)
    )
    return canonical, tags


def digest_lines(lines: tuple[bytes, ...]) -> str:
    digest = hashlib.sha256()
    for line in lines:
        digest.update(len(line).to_bytes(8, "big", signed=False))
        digest.update(line)
    return digest.hexdigest()


class HeaderAccumulator:
    def __init__(self) -> None:
        self._references: list[bytes] = []
        self._read_groups: list[bytes] = []
        self._reference_index: dict[bytes, int] = {}

    def add(self, raw_line: bytes) -> None:
        if raw_line.startswith(b"@SQ\t"):
            canonical, tags = canonical_header_record(raw_line, b"@SQ")
            name = tags.get(b"SN")
            if name is None or not name:
                raise PartitionCheckError("SAM @SQ record does not define SN")
            if name in self._reference_index:
                raise PartitionCheckError("SAM header contains duplicate @SQ SN values")
            if len(self._references) > UINT32_MAX:
                raise PartitionCheckError("SAM header has too many reference sequences")
            self._reference_index[name] = len(self._references)
            self._references.append(canonical)
        elif raw_line.startswith(b"@RG\t"):
            canonical, _ = canonical_header_record(raw_line, b"@RG")
            self._read_groups.append(canonical)
        elif raw_line.startswith((b"@HD\t", b"@PG\t", b"@CO\t")):
            return
        elif raw_line.startswith(b"@"):
            raise PartitionCheckError("SAM header contains an unsupported record type")
        else:
            raise PartitionCheckError("SAM header contains a malformed record")

    def finish(self) -> HeaderMetadata:
        references = tuple(self._references)
        # @SQ order defines reference order; @RG order does not.
        read_groups = tuple(sorted(self._read_groups))
        return HeaderMetadata(
            reference_records=references,
            read_group_records=read_groups,
            reference_index=dict(self._reference_index),
            reference_dictionary_sha256=digest_lines(references),
            read_group_dictionary_sha256=digest_lines(read_groups),
        )


def parse_cigar(cigar: bytes, record_number: int) -> list[tuple[int, int]]:
    if not cigar or cigar == b"*":
        raise PartitionCheckError(
            f"eligible mapped record {record_number} does not have a CIGAR"
        )
    elements: list[tuple[int, int]] = []
    consumed = 0
    for match in CIGAR_PATTERN.finditer(cigar):
        if match.start() != consumed:
            raise PartitionCheckError(
                f"eligible mapped record {record_number} has an invalid CIGAR"
            )
        length = int(match.group(1))
        if length <= 0:
            raise PartitionCheckError(
                f"eligible mapped record {record_number} has an invalid CIGAR"
            )
        elements.append((length, match.group(2)[0]))
        consumed = match.end()
    if not elements or consumed != len(cigar):
        raise PartitionCheckError(
            f"eligible mapped record {record_number} has an invalid CIGAR"
        )
    return elements


def unclipped_coordinate(
    position: int,
    cigar: bytes,
    reverse: bool,
    record_number: int,
) -> int:
    if position <= 0 or position > INT32_MAX:
        raise PartitionCheckError(
            f"eligible mapped record {record_number} has an invalid position"
        )
    elements = parse_cigar(cigar, record_number)
    if reverse:
        reference_length = sum(
            length for length, operator in elements if operator in REFERENCE_CONSUMING
        )
        if reference_length <= 0:
            raise PartitionCheckError(
                f"eligible mapped record {record_number} has no reference-consuming CIGAR operation"
            )
        coordinate = position + reference_length - 1
        for length, operator in reversed(elements):
            if operator not in CLIPPING:
                break
            coordinate += length
    else:
        coordinate = position
        for length, operator in elements:
            if operator not in CLIPPING:
                break
            coordinate -= length
    if coordinate < INT32_MIN or coordinate > INT32_MAX:
        raise PartitionCheckError(
            f"eligible mapped record {record_number} has an out-of-range unclipped coordinate"
        )
    return coordinate


def extract_umi(
    read_name: bytes,
    separator: bytes,
    umi_length: int,
    record_number: int,
) -> bytes:
    """Mirror SAMRead's greedy literal-separator UMI extraction."""

    search_end = len(read_name)
    while True:
        separator_start = read_name.rfind(separator, 0, search_end)
        if separator_start < 0:
            raise PartitionCheckError(
                f"eligible mapped record {record_number} has no parseable QNAME UMI"
            )
        umi_start = separator_start + len(separator)
        if umi_start < len(read_name) and read_name[umi_start] in UMI_BASES:
            break
        search_end = separator_start

    umi_end = umi_start
    while umi_end < len(read_name) and read_name[umi_end] in UMI_BASES:
        umi_end += 1
    available = umi_end - umi_start
    if available < umi_length:
        raise PartitionCheckError(
            f"eligible mapped record {record_number} has a shorter-than-requested QNAME UMI"
        )
    return read_name[umi_start : umi_start + umi_length].upper()


def record_cluster_tags(
    fields: list[bytes],
    record_number: int,
    umi_length: int,
) -> tuple[int, bytes]:
    observed_mi: bytes | None = None
    observed_rx: bytes | None = None
    for field in fields[11:]:
        if not field.startswith((b"MI:", b"RX:")):
            continue
        parts = field.split(b":", 2)
        if len(parts) != 3:
            raise PartitionCheckError(
                f"eligible mapped record {record_number} has a malformed cluster tag"
            )
        tag, value_type, value = parts
        if value_type != b"Z":
            raise PartitionCheckError(
                f"eligible mapped record {record_number} has a non-Z cluster tag"
            )
        if tag == b"MI":
            if observed_mi is not None:
                raise PartitionCheckError(
                    f"eligible mapped record {record_number} has duplicate MI tags"
                )
            observed_mi = value
        else:
            if observed_rx is not None:
                raise PartitionCheckError(
                    f"eligible mapped record {record_number} has duplicate RX tags"
                )
            observed_rx = value
    if observed_mi is None:
        raise PartitionCheckError(
            f"eligible mapped record {record_number} does not have an MI tag"
        )
    if observed_rx is None:
        raise PartitionCheckError(
            f"eligible mapped record {record_number} does not have an RX tag"
        )
    if not MI_PATTERN.fullmatch(observed_mi):
        raise PartitionCheckError(
            f"eligible mapped record {record_number} has a non-numeric MI tag"
        )
    mi = int(observed_mi)
    if mi > INT32_MAX:
        raise PartitionCheckError(
            f"eligible mapped record {record_number} has an out-of-range MI tag"
        )
    if len(observed_rx) != umi_length or any(base not in UMI_BASES for base in observed_rx):
        raise PartitionCheckError(
            f"eligible mapped record {record_number} has an invalid RX tag"
        )
    return mi, observed_rx.upper()


def reference_identifier(
    reference_name: bytes,
    reference_index: dict[bytes, int],
    record_number: int,
) -> int:
    if reference_name == b"*":
        raise PartitionCheckError(
            f"eligible mapped record {record_number} does not name a reference"
        )
    try:
        return reference_index[reference_name]
    except KeyError as error:
        raise PartitionCheckError(
            f"eligible mapped record {record_number} names a reference absent from @SQ"
        ) from error


def eligible_record(
    fields: list[bytes],
    mode: str,
    remove_unpaired: bool,
    remove_chimeric: bool,
    metrics: RecordMetrics,
    record_number: int,
    umi_length: int,
) -> tuple[int, int, bytes, int, bytes, int, bytes] | None:
    if len(fields) < 11:
        raise PartitionCheckError(f"SAM record {record_number} has fewer than 11 fields")
    try:
        flag = int(fields[1])
        position = int(fields[3])
        template_length = int(fields[8])
    except ValueError as error:
        raise PartitionCheckError(
            f"SAM record {record_number} has a non-integer core field"
        ) from error
    if flag < 0 or flag > 0xFFFF or template_length < INT32_MIN or template_length > INT32_MAX:
        raise PartitionCheckError(
            f"SAM record {record_number} has an out-of-range core field"
        )

    metrics.input_records += 1
    paired_flag = bool(flag & FLAG_PAIRED)
    if mode == "paired" and paired_flag and flag & FLAG_SECOND:
        metrics.excluded_second_of_pair += 1
        return None
    if flag & FLAG_UNMAPPED:
        metrics.excluded_unmapped += 1
        return None
    if mode == "paired":
        if remove_unpaired and not paired_flag:
            metrics.excluded_unpaired += 1
            return None
        if paired_flag and flag & FLAG_MATE_UNMAPPED:
            metrics.excluded_mate_unmapped += 1
            return None
        if remove_chimeric and paired_flag:
            mate_reference = fields[6]
            if mate_reference == b"=":
                mate_reference = fields[2]
            if fields[2] != mate_reference:
                metrics.excluded_chimeric += 1
                return None

    mi, rx = record_cluster_tags(fields, record_number, umi_length)
    return (
        flag,
        position,
        fields[5],
        template_length,
        fields[0],
        mi,
        rx,
    )


@contextmanager
def external_sort_sink(
    destination: Path,
    *,
    sort_command: str,
    sort_buffer_size: str,
    temporary_directory: Path,
    label: str,
) -> Iterator[BinaryIO]:
    stderr_path = temporary_directory / f"{label}.sort.stderr"
    with secure_binary_output(destination) as output, secure_binary_output(stderr_path) as error:
        environment = deterministic_environment(temporary_directory)
        try:
            process = subprocess.Popen(
                [
                    sort_command,
                    "--buffer-size",
                    sort_buffer_size,
                    "--temporary-directory",
                    os.fspath(temporary_directory),
                ],
                stdin=subprocess.PIPE,
                stdout=output,
                stderr=error,
                env=environment,
            )
        except OSError as start_error:
            raise PartitionCheckError("external sort could not be started") from start_error
        assert process.stdin is not None
        try:
            yield process.stdin
            process.stdin.close()
            return_code = process.wait()
        except BaseException as producer_error:
            try:
                process.stdin.close()
            except OSError:
                pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if isinstance(producer_error, BrokenPipeError):
                raise PartitionCheckError("external sort failed") from producer_error
            raise
        if return_code != 0:
            raise PartitionCheckError("external sort failed")


def alignment_token(
    reference_id: int,
    reverse: bool,
    coordinate: int,
    template_length: int | None,
) -> bytes:
    token = (
        f"{reference_id:08x}{1 if reverse else 0}{coordinate + SIGNED_OFFSET:08x}"
    )
    if template_length is not None:
        token += f"{template_length + SIGNED_OFFSET:08x}"
    return token.encode("ascii")


def extract_sorted_record_keys(
    source: Path,
    destination: Path,
    *,
    label: str,
    samtools: str,
    sort_command: str,
    sort_buffer_size: str,
    temporary_directory: Path,
    mode: str,
    remove_unpaired: bool,
    remove_chimeric: bool,
    separator: bytes,
    umi_length: int,
) -> tuple[HeaderMetadata, RecordMetrics]:
    view_stderr = temporary_directory / f"{label}.samtools.stderr"
    metrics = RecordMetrics()
    header_accumulator = HeaderAccumulator()
    header: HeaderMetadata | None = None

    with (
        secure_binary_output(view_stderr) as view_error,
        external_sort_sink(
            destination,
            sort_command=sort_command,
            sort_buffer_size=sort_buffer_size,
            temporary_directory=temporary_directory,
            label=f"{label}.records",
        ) as sort_input,
    ):
        try:
            view = subprocess.Popen(
                [samtools, "view", "-h", os.fspath(source)],
                stdout=subprocess.PIPE,
                stderr=view_error,
                env=deterministic_environment(temporary_directory),
            )
        except OSError as error:
            raise PartitionCheckError("samtools could not be started") from error
        assert view.stdout is not None
        try:
            record_number = 0
            for raw_line in view.stdout:
                if header is None and raw_line.startswith(b"@"):
                    header_accumulator.add(raw_line)
                    continue
                if header is None:
                    header = header_accumulator.finish()
                record_number += 1
                if not raw_line.endswith(b"\n"):
                    raise PartitionCheckError(
                        f"SAM record {record_number} lacks a final newline"
                    )
                fields = raw_line[:-1].rstrip(b"\r").split(b"\t")
                eligible = eligible_record(
                    fields,
                    mode,
                    remove_unpaired,
                    remove_chimeric,
                    metrics,
                    record_number,
                    umi_length,
                )
                if eligible is None:
                    continue
                flag, position, cigar, template_length, read_name, mi, rx = eligible
                assert header is not None
                reference_id = reference_identifier(
                    fields[2], header.reference_index, record_number
                )
                coordinate = unclipped_coordinate(
                    position,
                    cigar,
                    bool(flag & FLAG_REVERSE),
                    record_number,
                )
                umi = extract_umi(read_name, separator, umi_length, record_number)
                token = alignment_token(
                    reference_id,
                    bool(flag & FLAG_REVERSE),
                    coordinate,
                    template_length if mode == "paired" else None,
                )
                sort_input.write(token)
                sort_input.write(b"\t")
                sort_input.write(f"{mi:08x}".encode("ascii"))
                sort_input.write(b"\t")
                sort_input.write(umi)
                sort_input.write(b"\t")
                sort_input.write(rx)
                sort_input.write(b"\n")
                metrics.eligible_records += 1
            if header is None:
                header = header_accumulator.finish()
            view.stdout.close()
            view_return_code = view.wait()
        except BaseException:
            view.stdout.close()
            view.terminate()
            try:
                view.wait(timeout=5)
            except subprocess.TimeoutExpired:
                view.kill()
                view.wait()
            raise
        if view_return_code != 0:
            raise PartitionCheckError("samtools failed while decoding a tagged input")

    return header, metrics


def parse_record_key_line(
    raw_line: bytes,
    line_number: int,
) -> tuple[bytes, bytes, bytes, bytes]:
    if not raw_line.endswith(b"\n"):
        raise PartitionCheckError(
            f"private record-key row {line_number} lacks a final newline"
        )
    fields = raw_line[:-1].split(b"\t")
    if len(fields) != 4:
        raise PartitionCheckError(f"private record-key row {line_number} is malformed")
    return fields[0], fields[1], fields[2], fields[3]


def emit_cluster(
    destination: BinaryIO,
    alignment: bytes,
    umi_counts: list[tuple[bytes, int]],
) -> None:
    destination.write(alignment)
    destination.write(b"\t")
    for index, (umi, frequency) in enumerate(umi_counts):
        if index:
            destination.write(b",")
        destination.write(umi)
        destination.write(b":")
        destination.write(str(frequency).encode("ascii"))
    destination.write(b"\n")


def canonicalize_clusters(
    sorted_record_keys: Path,
    destination: Path,
    *,
    sort_command: str,
    sort_buffer_size: str,
    temporary_directory: Path,
    label: str,
) -> tuple[int, int, int, int, int]:
    records = 0
    clusters = 0
    umi_memberships = 0
    alignment_groups = 0
    max_umi_memberships = 0
    current_alignment: bytes | None = None
    current_mi: bytes | None = None
    current_umi: bytes | None = None
    current_rx: bytes | None = None
    current_frequency = 0
    current_cluster: list[tuple[bytes, int]] = []
    alignment_umi_owner: dict[bytes, bytes] = {}

    with (
        sorted_record_keys.open("rb") as source,
        external_sort_sink(
            destination,
            sort_command=sort_command,
            sort_buffer_size=sort_buffer_size,
            temporary_directory=temporary_directory,
            label=f"{label}.clusters",
        ) as sort_input,
    ):
        for line_number, raw_line in enumerate(source, start=1):
            records += 1
            alignment, mi, umi, rx = parse_record_key_line(raw_line, line_number)
            cluster_changed = (
                current_alignment is not None
                and (alignment != current_alignment or mi != current_mi)
            )
            if cluster_changed:
                assert current_umi is not None
                assert current_rx is not None
                current_cluster.append((current_umi, current_frequency))
                if not any(member == current_rx for member, _ in current_cluster):
                    raise PartitionCheckError(
                        "an MI cluster names an RX root absent from its UMI membership"
                    )
                emit_cluster(sort_input, current_alignment, current_cluster)
                clusters += 1
                umi_memberships += len(current_cluster)
                max_umi_memberships = max(max_umi_memberships, len(current_cluster))
                current_cluster = []
                current_umi = None
                current_rx = None
                current_frequency = 0
            if current_alignment is None or alignment != current_alignment:
                alignment_groups += 1
                alignment_umi_owner = {}
            if cluster_changed or current_alignment is None:
                current_alignment = alignment
                current_mi = mi
            owner = alignment_umi_owner.get(umi)
            if owner is None:
                alignment_umi_owner[umi] = mi
            elif owner != mi:
                raise PartitionCheckError(
                    "one alignment-group UMI is split across multiple MI clusters"
                )
            if current_rx is None:
                current_rx = rx
            elif current_rx != rx:
                raise PartitionCheckError(
                    "one MI cluster contains inconsistent RX roots"
                )
            if current_umi is None:
                current_umi = umi
                current_frequency = 1
            elif umi == current_umi:
                current_frequency += 1
            else:
                current_cluster.append((current_umi, current_frequency))
                current_umi = umi
                current_frequency = 1

        if current_alignment is not None:
            assert current_umi is not None
            assert current_rx is not None
            current_cluster.append((current_umi, current_frequency))
            if not any(member == current_rx for member, _ in current_cluster):
                raise PartitionCheckError(
                    "an MI cluster names an RX root absent from its UMI membership"
                )
            emit_cluster(sort_input, current_alignment, current_cluster)
            clusters += 1
            umi_memberships += len(current_cluster)
            max_umi_memberships = max(max_umi_memberships, len(current_cluster))

    return records, alignment_groups, clusters, umi_memberships, max_umi_memberships


def file_digest(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    final_byte = b""
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
            final_byte = chunk[-1:]
    if size and final_byte != b"\n":
        raise PartitionCheckError("private canonical partition lacks a final newline")
    return size, digest.hexdigest()


def files_equal(left: Path, right: Path) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as left_stream, right.open("rb") as right_stream:
        while True:
            left_chunk = left_stream.read(1024 * 1024)
            right_chunk = right_stream.read(1024 * 1024)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


def process_input(
    source: Path,
    *,
    label: str,
    samtools: str,
    sort_command: str,
    sort_buffer_size: str,
    workspace: Path,
    mode: str,
    remove_unpaired: bool,
    remove_chimeric: bool,
    separator: bytes,
    umi_length: int,
) -> tuple[HeaderMetadata, PartitionMetrics, Path]:
    record_keys = workspace / f"{label}.record-keys.sorted"
    canonical_partition = workspace / f"{label}.partitions.sorted"
    header, record_metrics = extract_sorted_record_keys(
        source,
        record_keys,
        label=label,
        samtools=samtools,
        sort_command=sort_command,
        sort_buffer_size=sort_buffer_size,
        temporary_directory=workspace,
        mode=mode,
        remove_unpaired=remove_unpaired,
        remove_chimeric=remove_chimeric,
        separator=separator,
        umi_length=umi_length,
    )
    record_key_bytes = record_keys.stat().st_size
    (
        canonicalized_records,
        alignment_groups,
        clusters,
        umi_memberships,
        max_umi_memberships,
    ) = canonicalize_clusters(
        record_keys,
        canonical_partition,
        sort_command=sort_command,
        sort_buffer_size=sort_buffer_size,
        temporary_directory=workspace,
        label=label,
    )
    if canonicalized_records != record_metrics.eligible_records:
        raise PartitionCheckError(
            "eligible record count changed during private canonicalization"
        )
    if canonicalized_records == 0:
        raise PartitionCheckError(
            "tagged input has no eligible mapped records to compare"
        )
    try:
        record_keys.unlink()
    except OSError as error:
        raise PartitionCheckError("private record-key file could not be removed") from error
    canonical_bytes, partition_digest = file_digest(canonical_partition)
    metrics = PartitionMetrics(
        input_records=record_metrics.input_records,
        eligible_records=record_metrics.eligible_records,
        excluded_unmapped=record_metrics.excluded_unmapped,
        excluded_second_of_pair=record_metrics.excluded_second_of_pair,
        excluded_unpaired=record_metrics.excluded_unpaired,
        excluded_mate_unmapped=record_metrics.excluded_mate_unmapped,
        excluded_chimeric=record_metrics.excluded_chimeric,
        alignment_groups=alignment_groups,
        clusters=clusters,
        umi_memberships=umi_memberships,
        max_umi_memberships_per_cluster=max_umi_memberships,
        record_key_bytes=record_key_bytes,
        canonical_partition_bytes=canonical_bytes,
        partition_cluster_multiset_sha256=partition_digest,
        reference_sequences=len(header.reference_records),
        reference_dictionary_sha256=header.reference_dictionary_sha256,
        read_groups=len(header.read_group_records),
        read_group_dictionary_sha256=header.read_group_dictionary_sha256,
    )
    return header, metrics, canonical_partition


def metrics_receipt(metrics: PartitionMetrics) -> dict[str, int | str]:
    return {
        "input_records": metrics.input_records,
        "eligible_records": metrics.eligible_records,
        "excluded_unmapped": metrics.excluded_unmapped,
        "excluded_second_of_pair": metrics.excluded_second_of_pair,
        "excluded_unpaired": metrics.excluded_unpaired,
        "excluded_mate_unmapped": metrics.excluded_mate_unmapped,
        "excluded_chimeric": metrics.excluded_chimeric,
        "alignment_groups": metrics.alignment_groups,
        "clusters": metrics.clusters,
        "umi_memberships": metrics.umi_memberships,
        "max_umi_memberships_per_cluster": metrics.max_umi_memberships_per_cluster,
        "record_key_bytes": metrics.record_key_bytes,
        "canonical_partition_bytes": metrics.canonical_partition_bytes,
        "partition_cluster_multiset_sha256": (
            metrics.partition_cluster_multiset_sha256
        ),
        "reference_sequences": metrics.reference_sequences,
        "reference_dictionary_sha256": metrics.reference_dictionary_sha256,
        "read_groups": metrics.read_groups,
        "read_group_dictionary_sha256": metrics.read_group_dictionary_sha256,
    }


def compare(
    left: Path,
    right: Path,
    *,
    samtools: str,
    sort_command: str,
    sort_buffer_size: str,
    temporary_parent: Path | None,
    mode: str,
    remove_unpaired: bool,
    remove_chimeric: bool,
    separator: bytes,
    umi_length: int,
) -> dict[str, object]:
    left_identity = input_identity(left, "left")
    right_identity = input_identity(right, "right")
    if (
        left_identity.device == right_identity.device
        and left_identity.inode == right_identity.inode
    ):
        raise PartitionCheckError(
            "left and right inputs resolve to the same file or hardlink"
        )

    with private_workspace(temporary_parent) as workspace:
        left_header, left_metrics, left_partition = process_input(
            left,
            label="left",
            samtools=samtools,
            sort_command=sort_command,
            sort_buffer_size=sort_buffer_size,
            workspace=workspace,
            mode=mode,
            remove_unpaired=remove_unpaired,
            remove_chimeric=remove_chimeric,
            separator=separator,
            umi_length=umi_length,
        )
        right_header, right_metrics, right_partition = process_input(
            right,
            label="right",
            samtools=samtools,
            sort_command=sort_command,
            sort_buffer_size=sort_buffer_size,
            workspace=workspace,
            mode=mode,
            remove_unpaired=remove_unpaired,
            remove_chimeric=remove_chimeric,
            separator=separator,
            umi_length=umi_length,
        )
        validate_unchanged(left, left_identity, "left")
        validate_unchanged(right, right_identity, "right")

        reference_dictionary_equivalent = (
            left_header.reference_records == right_header.reference_records
        )
        read_group_dictionary_equivalent = (
            left_header.read_group_records == right_header.read_group_records
        )
        partition_equivalent = files_equal(left_partition, right_partition)
        equivalent = (
            reference_dictionary_equivalent
            and read_group_dictionary_equivalent
            and partition_equivalent
        )
        # The persistent-file estimate excludes sort's bounded memory buffer and
        # transient merge files, whose aggregate is at most on the order of the
        # stream currently being sorted.
        peak_persistent_bytes_upper_bound = (
            left_metrics.canonical_partition_bytes
            + max(
                right_metrics.record_key_bytes
                + right_metrics.canonical_partition_bytes,
                left_metrics.record_key_bytes
                + left_metrics.canonical_partition_bytes,
            )
        )
        return {
            "schema": SCHEMA,
            "partition_fingerprint_version": PARTITION_VERSION,
            "equivalent": equivalent,
            "partition_equivalent": partition_equivalent,
            "reference_dictionary_equivalent": reference_dictionary_equivalent,
            "read_group_dictionary_equivalent": read_group_dictionary_equivalent,
            "configuration": {
                "mode": mode,
                "umi_length": umi_length,
                "umi_separator_bytes": len(separator),
                "umi_separator_sha256": hashlib.sha256(separator).hexdigest(),
                "remove_unpaired": remove_unpaired,
                "remove_chimeric": remove_chimeric,
                "sort_buffer_size": sort_buffer_size,
            },
            "left": metrics_receipt(left_metrics),
            "right": metrics_receipt(right_metrics),
            "temporary_storage": {
                "persistent_stage_peak_upper_bound_bytes": (
                    peak_persistent_bytes_upper_bound
                ),
                "sort_merge_storage_note": (
                    "bounded external-sort merge files are additional and "
                    "scale linearly with the active stage"
                ),
            },
        }


def atomic_write_json_noreplace(destination: Path, payload: dict[str, object]) -> None:
    parent = destination.parent
    try:
        parent_metadata = parent.stat()
    except OSError as error:
        raise PartitionCheckError("receipt parent directory does not exist") from error
    if not stat.S_ISDIR(parent_metadata.st_mode):
        raise PartitionCheckError("receipt parent is not a directory")

    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=os.fspath(parent),
        )
        temporary = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, destination, follow_symlinks=False)
        except FileExistsError as error:
            raise PartitionCheckError(
                "receipt destination already exists; refusing to overwrite it"
            ) from error
        directory_descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except PartitionCheckError:
        raise
    except OSError as error:
        raise PartitionCheckError("receipt could not be published atomically") from error
    finally:
        if "temporary" in locals():
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare UMI cluster partitions in two UMICollapse --tag SAM/BAM outputs."
        )
    )
    parser.add_argument("left", help="first private tagged SAM/BAM")
    parser.add_argument("right", help="second private tagged SAM/BAM")
    parser.add_argument(
        "--receipt",
        required=True,
        help="new JSON receipt path; existing destinations are never overwritten",
    )
    parser.add_argument(
        "--umi-length",
        required=True,
        type=int,
        help="positive QNAME UMI length used for both tagged runs",
    )
    parser.add_argument(
        "--umi-separator",
        default="_",
        help="literal QNAME separator immediately before the UMI (default: _)",
    )
    parser.add_argument(
        "--mode",
        choices=MODES,
        default="single-end",
        help="UMICollapse alignment-key mode (default: single-end)",
    )
    parser.add_argument(
        "--remove-unpaired",
        action="store_true",
        help="in paired mode, mirror UMICollapse --remove-unpaired eligibility",
    )
    parser.add_argument(
        "--remove-chimeric",
        action="store_true",
        help="in paired mode, mirror UMICollapse --remove-chimeric eligibility",
    )
    parser.add_argument(
        "--tmpdir",
        "--temp-dir",
        dest="tmpdir",
        help="parent for the private external-sort workspace",
    )
    parser.add_argument(
        "--samtools",
        default="samtools",
        help="samtools executable (default: samtools)",
    )
    parser.add_argument(
        "--sort-command",
        default=None,
        help=(
            "GNU coreutils sort executable "
            "(default: auto-detect gsort, then sort)"
        ),
    )
    parser.add_argument(
        "--sort-buffer-size",
        default="256M",
        help="bounded memory supplied to sort --buffer-size (default: 256M)",
    )
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    parsed = parse_arguments(arguments)
    try:
        if parsed.umi_length <= 0:
            raise PartitionCheckError("--umi-length must be positive")
        if not parsed.umi_separator:
            raise PartitionCheckError("--umi-separator must not be empty")
        try:
            separator = parsed.umi_separator.encode("ascii")
        except UnicodeEncodeError as error:
            raise PartitionCheckError("--umi-separator must be ASCII") from error
        if b"\t" in separator or b"\r" in separator or b"\n" in separator:
            raise PartitionCheckError("--umi-separator contains a forbidden character")
        if not SORT_BUFFER_PATTERN.fullmatch(parsed.sort_buffer_size):
            raise PartitionCheckError("--sort-buffer-size has an invalid format")
        if parsed.mode != "paired" and (
            parsed.remove_unpaired or parsed.remove_chimeric
        ):
            raise PartitionCheckError(
                "paired eligibility flags require --mode paired"
            )

        left = Path(parsed.left).resolve()
        right = Path(parsed.right).resolve()
        receipt = Path(parsed.receipt).absolute()
        temporary_parent = Path(parsed.tmpdir).resolve() if parsed.tmpdir else None
        samtools = command_path(parsed.samtools, "samtools")
        sort_command = resolve_gnu_sort(parsed.sort_command)
        result = compare(
            left,
            right,
            samtools=samtools,
            sort_command=sort_command,
            sort_buffer_size=parsed.sort_buffer_size,
            temporary_parent=temporary_parent,
            mode=parsed.mode,
            remove_unpaired=parsed.remove_unpaired,
            remove_chimeric=parsed.remove_chimeric,
            separator=separator,
            umi_length=parsed.umi_length,
        )
        atomic_write_json_noreplace(receipt, result)
        return 0 if result["equivalent"] else 1
    except PartitionCheckError as error:
        print(f"cluster partition check error: {error}", file=sys.stderr)
        return 2


def cli_entrypoint() -> int:
    """Run the command with scheduler-signal cleanup enabled."""
    install_termination_signal_handlers()
    try:
        return main()
    except PartitionSignalInterrupt as error:
        signal_name = signal.Signals(error.signal_number).name
        print(
            f"cluster partition check error: interrupted by {signal_name}",
            file=sys.stderr,
        )
        return 128 + error.signal_number


if __name__ == "__main__":
    os.umask(0o077)
    raise SystemExit(cli_entrypoint())
