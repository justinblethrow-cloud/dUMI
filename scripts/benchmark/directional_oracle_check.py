#!/usr/bin/env python3
"""Adjudicate tagged Directional clusters against an independent source oracle.

The source is an immutable pre-deduplication SAM/BAM.  The other two inputs
are ``--tag`` outputs from canonical UMICollapse and dUMI ``--streaming-mode
off``.  This helper reconstructs Directional clusters from source QNAME UMIs
with ordinary string Hamming distance; production UMI-distance data
structures are not used.

All input-derived streams live in a private temporary directory and are
removed on normal completion, handled errors, SIGHUP, and SIGTERM.  The JSON
receipt contains aggregate counts and private fingerprints only.  Exit status
is zero only when dUMI agrees with the independent oracle for membership,
root assignment, the ordered ``@SQ`` dictionary, and the normalized
order-independent ``@RG`` dictionary.  Canonical
upstream comparisons are diagnostic and never gate success.
"""

from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
import math
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
from typing import BinaryIO, Iterator, Sequence


SCHEMA = "dumi-directional-oracle-check-v1"
SCHEMA_VERSION = 1
ORACLE_VERSION = "string-hamming-directional-v1"
ROOT_ORDER_VERSION = "dumi-bitset-signed-chunks-v1"
THRESHOLD_VERSION = "java-binary32-directional-threshold-v1"
ROOTED_PARTITION_VERSION = "alignment-root-umi-frequency-v1"
MEMBERSHIP_PARTITION_VERSION = "alignment-cluster-umi-frequency-v1"
BASE_ENCODING = {
    ord("A"): 0b000,
    ord("T"): 0b101,
    ord("C"): 0b110,
    ord("G"): 0b011,
    ord("N"): 0b100,
}
UMI_ALPHABET = tuple(BASE_ENCODING)


class OracleCheckError(RuntimeError):
    """An input, oracle, or private-workspace invariant failed."""


def load_partition_checker(path: Path):
    """Load the colocated parsing/sort helper without importing production code."""

    specification = importlib.util.spec_from_file_location(
        "dumi_directional_partition_checker",
        path,
    )
    if specification is None or specification.loader is None:
        raise OracleCheckError("cluster partition checker could not be loaded")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def binary32(value: float) -> float:
    """Round a Python number exactly to Java ``float`` precision."""

    try:
        return struct.unpack(">f", struct.pack(">f", value))[0]
    except OverflowError:
        return math.copysign(math.inf, value)


def directional_threshold(
    frequency: int,
    percentage: float,
    *,
    int32_min: int = -(1 << 31),
    int32_max: int = (1 << 31) - 1,
) -> int:
    """Mirror dUMI's Java binary32 threshold calculation."""

    if frequency < 0 or frequency > int32_max:
        raise OracleCheckError("oracle frequency is outside Java int range")
    percentage32 = binary32(percentage)
    incremented32 = binary32(float(frequency + 1))
    threshold32 = binary32(percentage32 * incremented32)
    if threshold32 >= int32_max:
        return int32_max
    if threshold32 <= int32_min:
        return int32_min
    return math.trunc(threshold32)


def java_signed_long(value: int) -> int:
    value &= (1 << 64) - 1
    return value - (1 << 64) if value & (1 << 63) else value


def encoded_tie_key(umi: bytes) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Mirror dUMI ``BitSet.compareTo`` at any fixed, equal UMI length."""

    bit_length = len(umi) * 3
    chunk_count = (bit_length + 63) // 64
    bits = [0] * chunk_count
    n_bits = [0] * chunk_count
    for base_index, base in enumerate(umi):
        try:
            value = BASE_ENCODING[base]
        except KeyError as error:
            raise OracleCheckError("oracle encountered an invalid UMI base") from error
        for bit_offset in range(3):
            absolute = base_index * 3 + bit_offset
            chunk = absolute // 64
            mask = 1 << (absolute % 64)
            if value & (1 << bit_offset):
                bits[chunk] |= mask
            if base == ord("N"):
                n_bits[chunk] |= mask
    return (
        tuple(java_signed_long(value) for value in bits),
        tuple(java_signed_long(value) for value in n_bits),
    )


def candidate_neighbors(umi: bytes, remaining: set[bytes]) -> list[bytes]:
    """Enumerate the exact Hamming-distance-one neighborhood over ATCGN."""

    candidates: set[bytes] = set()
    for index, observed_base in enumerate(umi):
        for replacement in UMI_ALPHABET:
            if replacement == observed_base:
                continue
            candidate = umi[:index] + bytes((replacement,)) + umi[index + 1 :]
            if candidate in remaining:
                candidates.add(candidate)
    return sorted(candidates, key=encoded_tie_key)


def directional_clusters(
    frequencies: dict[bytes, int],
    percentage: float,
) -> list[tuple[bytes, tuple[bytes, ...]]]:
    """Return independent Directional roots and membership closure."""

    lengths = {len(umi) for umi in frequencies}
    if len(lengths) != 1 or not lengths or next(iter(lengths)) <= 0:
        raise OracleCheckError(
            "oracle requires one positive, fixed UMI length per alignment group"
        )
    remaining = set(frequencies)
    ordered = sorted(
        frequencies,
        key=lambda umi: (-frequencies[umi], encoded_tie_key(umi)),
    )
    clusters: list[tuple[bytes, tuple[bytes, ...]]] = []
    for root in ordered:
        if root not in remaining:
            continue
        remaining.remove(root)
        members = [root]
        pending = [root]
        while pending:
            current = pending.pop()
            threshold = directional_threshold(frequencies[current], percentage)
            neighbors = [
                candidate
                for candidate in candidate_neighbors(current, remaining)
                if frequencies[candidate] <= threshold
            ]
            for candidate in neighbors:
                remaining.remove(candidate)
                members.append(candidate)
            pending.extend(reversed(neighbors))
        clusters.append((root, tuple(sorted(members))))
    if remaining:
        raise OracleCheckError("oracle left one or more UMIs unassigned")
    return clusters


def emit_partition(
    destination: BinaryIO,
    alignment: bytes,
    root: bytes | None,
    members: Sequence[tuple[bytes, int]],
) -> None:
    destination.write(alignment)
    destination.write(b"\t")
    if root is not None:
        destination.write(root)
        destination.write(b"\t")
    for index, (umi, frequency) in enumerate(members):
        if index:
            destination.write(b",")
        destination.write(umi)
        destination.write(b":")
        destination.write(str(frequency).encode("ascii"))
    destination.write(b"\n")


def emit_alignment_umi_frequency(
    destination: BinaryIO,
    alignment: bytes,
    umi: bytes,
    frequency: int,
) -> None:
    """Emit a cluster-independent alignment/UMI-frequency multiset row."""

    destination.write(alignment)
    destination.write(b"\t")
    destination.write(umi)
    destination.write(b":")
    destination.write(str(frequency).encode("ascii"))
    destination.write(b"\n")


def source_eligible_record(
    fields: list[bytes],
    *,
    checker,
    mode: str,
    remove_unpaired: bool,
    remove_chimeric: bool,
    metrics,
    record_number: int,
) -> tuple[int, int, bytes, int, bytes] | None:
    """Apply the tagged checker's eligibility rules without requiring MI/RX."""

    if len(fields) < 11:
        raise OracleCheckError(
            f"SAM record {record_number} has fewer than 11 fields"
        )
    try:
        flag = int(fields[1])
        position = int(fields[3])
        template_length = int(fields[8])
    except ValueError as error:
        raise OracleCheckError(
            f"SAM record {record_number} has a non-integer core field"
        ) from error
    if (
        flag < 0
        or flag > 0xFFFF
        or template_length < checker.INT32_MIN
        or template_length > checker.INT32_MAX
    ):
        raise OracleCheckError(
            f"SAM record {record_number} has an out-of-range core field"
        )

    metrics.input_records += 1
    paired_flag = bool(flag & checker.FLAG_PAIRED)
    if mode == "paired" and paired_flag and flag & checker.FLAG_SECOND:
        metrics.excluded_second_of_pair += 1
        return None
    if flag & checker.FLAG_UNMAPPED:
        metrics.excluded_unmapped += 1
        return None
    if mode == "paired":
        if remove_unpaired and not paired_flag:
            metrics.excluded_unpaired += 1
            return None
        if paired_flag and flag & checker.FLAG_MATE_UNMAPPED:
            metrics.excluded_mate_unmapped += 1
            return None
        if remove_chimeric and paired_flag:
            mate_reference = fields[6]
            if mate_reference == b"=":
                mate_reference = fields[2]
            if fields[2] != mate_reference:
                metrics.excluded_chimeric += 1
                return None
    return flag, position, fields[5], template_length, fields[0]


def extract_source_record_keys(
    source: Path,
    destination: Path,
    *,
    checker,
    samtools: str,
    sort_command: str,
    sort_buffer_size: str,
    workspace: Path,
    mode: str,
    remove_unpaired: bool,
    remove_chimeric: bool,
    separator: bytes,
    umi_length: int,
) -> tuple[object, object]:
    """Decode the source into sorted ``alignment, UMI`` rows."""

    stderr_path = workspace / "source.samtools.stderr"
    metrics = checker.RecordMetrics()
    header_accumulator = checker.HeaderAccumulator()
    header = None
    with (
        checker.secure_binary_output(stderr_path) as view_error,
        checker.external_sort_sink(
            destination,
            sort_command=sort_command,
            sort_buffer_size=sort_buffer_size,
            temporary_directory=workspace,
            label="source.records",
        ) as sort_input,
    ):
        try:
            view = subprocess.Popen(
                [samtools, "view", "-h", os.fspath(source)],
                stdout=subprocess.PIPE,
                stderr=view_error,
                env=checker.deterministic_environment(workspace),
            )
        except OSError as error:
            raise OracleCheckError("samtools could not decode the source") from error
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
                    raise OracleCheckError(
                        f"SAM record {record_number} lacks a final newline"
                    )
                fields = raw_line[:-1].rstrip(b"\r").split(b"\t")
                eligible = source_eligible_record(
                    fields,
                    checker=checker,
                    mode=mode,
                    remove_unpaired=remove_unpaired,
                    remove_chimeric=remove_chimeric,
                    metrics=metrics,
                    record_number=record_number,
                )
                if eligible is None:
                    continue
                flag, position, cigar, template_length, read_name = eligible
                assert header is not None
                reference_id = checker.reference_identifier(
                    fields[2],
                    header.reference_index,
                    record_number,
                )
                coordinate = checker.unclipped_coordinate(
                    position,
                    cigar,
                    bool(flag & checker.FLAG_REVERSE),
                    record_number,
                )
                umi = checker.extract_umi(
                    read_name,
                    separator,
                    umi_length,
                    record_number,
                )
                alignment = checker.alignment_token(
                    reference_id,
                    bool(flag & checker.FLAG_REVERSE),
                    coordinate,
                    template_length if mode == "paired" else None,
                )
                sort_input.write(alignment)
                sort_input.write(b"\t")
                sort_input.write(umi)
                sort_input.write(b"\n")
                metrics.eligible_records += 1
            if header is None:
                header = header_accumulator.finish()
            view.stdout.close()
            return_code = view.wait()
        except BaseException:
            view.stdout.close()
            if view.poll() is None:
                view.terminate()
                try:
                    view.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    view.kill()
                    view.wait()
            raise
        if return_code != 0:
            raise OracleCheckError("samtools failed while decoding the source")
    if metrics.eligible_records == 0:
        raise OracleCheckError("source has no eligible mapped records")
    return header, metrics


def canonicalize_source_oracle(
    sorted_record_keys: Path,
    membership_destination: Path,
    rooted_destination: Path,
    record_multiset_destination: Path,
    *,
    checker,
    sort_command: str,
    sort_buffer_size: str,
    workspace: Path,
    percentage: float,
) -> dict[str, int | str]:
    """Collapse source UMI counts into independent membership/root streams."""

    records = 0
    alignment_groups = 0
    clusters = 0
    memberships = 0
    max_memberships = 0
    current_alignment: bytes | None = None
    frequencies: Counter[bytes] = Counter()

    with (
        sorted_record_keys.open("rb") as source,
        checker.external_sort_sink(
            membership_destination,
            sort_command=sort_command,
            sort_buffer_size=sort_buffer_size,
            temporary_directory=workspace,
            label="oracle.membership",
        ) as membership_output,
        checker.external_sort_sink(
            rooted_destination,
            sort_command=sort_command,
            sort_buffer_size=sort_buffer_size,
            temporary_directory=workspace,
            label="oracle.rooted",
        ) as rooted_output,
        checker.external_sort_sink(
            record_multiset_destination,
            sort_command=sort_command,
            sort_buffer_size=sort_buffer_size,
            temporary_directory=workspace,
            label="oracle.record-multiset",
        ) as record_multiset_output,
    ):

        def flush_group() -> None:
            nonlocal alignment_groups, clusters, memberships, max_memberships
            if current_alignment is None:
                return
            if any(value > checker.INT32_MAX for value in frequencies.values()):
                raise OracleCheckError("source UMI frequency exceeds Java int range")
            for umi in sorted(frequencies):
                emit_alignment_umi_frequency(
                    record_multiset_output,
                    current_alignment,
                    umi,
                    frequencies[umi],
                )
            for root, member_umis in directional_clusters(
                dict(frequencies),
                percentage,
            ):
                member_counts = [
                    (umi, frequencies[umi]) for umi in member_umis
                ]
                emit_partition(
                    membership_output,
                    current_alignment,
                    None,
                    member_counts,
                )
                emit_partition(
                    rooted_output,
                    current_alignment,
                    root,
                    member_counts,
                )
                clusters += 1
                memberships += len(member_counts)
                max_memberships = max(max_memberships, len(member_counts))
            alignment_groups += 1

        for line_number, raw_line in enumerate(source, start=1):
            records += 1
            if not raw_line.endswith(b"\n"):
                raise OracleCheckError(
                    f"source record-key row {line_number} lacks a final newline"
                )
            fields = raw_line[:-1].split(b"\t")
            if len(fields) != 2:
                raise OracleCheckError(
                    f"source record-key row {line_number} is malformed"
                )
            alignment, umi = fields
            if current_alignment is not None and alignment != current_alignment:
                flush_group()
                frequencies = Counter()
            current_alignment = alignment
            frequencies[umi] += 1
        flush_group()

    membership_bytes, membership_hash = checker.file_digest(
        membership_destination
    )
    rooted_bytes, rooted_hash = checker.file_digest(rooted_destination)
    record_multiset_bytes, record_multiset_hash = checker.file_digest(
        record_multiset_destination
    )
    return {
        "records": records,
        "alignment_groups": alignment_groups,
        "clusters": clusters,
        "umi_memberships": memberships,
        "max_umi_memberships_per_cluster": max_memberships,
        "membership_partition_bytes": membership_bytes,
        "membership_partition_sha256": membership_hash,
        "rooted_partition_bytes": rooted_bytes,
        "rooted_partition_sha256": rooted_hash,
        "alignment_umi_frequency_multiset_bytes": record_multiset_bytes,
        "alignment_umi_frequency_multiset_sha256": record_multiset_hash,
    }


def canonicalize_tagged(
    sorted_record_keys: Path,
    membership_destination: Path,
    rooted_destination: Path,
    record_multiset_destination: Path,
    *,
    checker,
    sort_command: str,
    sort_buffer_size: str,
    workspace: Path,
    label: str,
) -> dict[str, int | str]:
    """Discard MI values while preserving exact membership and RX roots."""

    records = 0
    alignment_groups = 0
    clusters = 0
    memberships = 0
    max_memberships = 0
    current_alignment: bytes | None = None
    current_mi: bytes | None = None
    current_umi: bytes | None = None
    current_root: bytes | None = None
    current_frequency = 0
    current_members: list[tuple[bytes, int]] = []
    alignment_umi_owner: dict[bytes, bytes] = {}

    with (
        sorted_record_keys.open("rb") as source,
        checker.external_sort_sink(
            membership_destination,
            sort_command=sort_command,
            sort_buffer_size=sort_buffer_size,
            temporary_directory=workspace,
            label=f"{label}.membership",
        ) as membership_output,
        checker.external_sort_sink(
            rooted_destination,
            sort_command=sort_command,
            sort_buffer_size=sort_buffer_size,
            temporary_directory=workspace,
            label=f"{label}.rooted",
        ) as rooted_output,
        checker.external_sort_sink(
            record_multiset_destination,
            sort_command=sort_command,
            sort_buffer_size=sort_buffer_size,
            temporary_directory=workspace,
            label=f"{label}.record-multiset",
        ) as record_multiset_output,
    ):

        def flush_cluster() -> None:
            nonlocal clusters, memberships, max_memberships
            if (
                current_alignment is None
                or current_umi is None
                or current_root is None
            ):
                raise OracleCheckError("tagged cluster state is incomplete")
            members = current_members + [(current_umi, current_frequency)]
            members.sort()
            if not any(member == current_root for member, _ in members):
                raise OracleCheckError(
                    "tagged cluster root is absent from its membership"
                )
            for member, frequency in members:
                emit_alignment_umi_frequency(
                    record_multiset_output,
                    current_alignment,
                    member,
                    frequency,
                )
            emit_partition(
                membership_output,
                current_alignment,
                None,
                members,
            )
            emit_partition(
                rooted_output,
                current_alignment,
                current_root,
                members,
            )
            clusters += 1
            memberships += len(members)
            max_memberships = max(max_memberships, len(members))

        for line_number, raw_line in enumerate(source, start=1):
            records += 1
            alignment, mi, umi, root = checker.parse_record_key_line(
                raw_line,
                line_number,
            )
            cluster_changed = (
                current_alignment is not None
                and (alignment != current_alignment or mi != current_mi)
            )
            if cluster_changed:
                flush_cluster()
                current_members = []
                current_umi = None
                current_root = None
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
                raise OracleCheckError(
                    "one alignment-group UMI is split across tagged clusters"
                )
            if current_root is None:
                current_root = root
            elif current_root != root:
                raise OracleCheckError(
                    "one tagged cluster contains inconsistent RX roots"
                )
            if current_umi is None:
                current_umi = umi
                current_frequency = 1
            elif umi == current_umi:
                current_frequency += 1
            else:
                current_members.append((current_umi, current_frequency))
                current_umi = umi
                current_frequency = 1
        if current_alignment is not None:
            flush_cluster()

    membership_bytes, membership_hash = checker.file_digest(
        membership_destination
    )
    rooted_bytes, rooted_hash = checker.file_digest(rooted_destination)
    record_multiset_bytes, record_multiset_hash = checker.file_digest(
        record_multiset_destination
    )
    return {
        "records": records,
        "alignment_groups": alignment_groups,
        "clusters": clusters,
        "umi_memberships": memberships,
        "max_umi_memberships_per_cluster": max_memberships,
        "membership_partition_bytes": membership_bytes,
        "membership_partition_sha256": membership_hash,
        "rooted_partition_bytes": rooted_bytes,
        "rooted_partition_sha256": rooted_hash,
        "alignment_umi_frequency_multiset_bytes": record_multiset_bytes,
        "alignment_umi_frequency_multiset_sha256": record_multiset_hash,
    }


def process_tagged(
    source: Path,
    *,
    checker,
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
) -> tuple[object, dict[str, int | str], Path, Path]:
    record_keys = workspace / f"{label}.record-keys.sorted"
    membership = workspace / f"{label}.membership.sorted"
    rooted = workspace / f"{label}.rooted.sorted"
    record_multiset = workspace / f"{label}.record-multiset.sorted"
    header, record_metrics = checker.extract_sorted_record_keys(
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
    metrics = canonicalize_tagged(
        record_keys,
        membership,
        rooted,
        record_multiset,
        checker=checker,
        sort_command=sort_command,
        sort_buffer_size=sort_buffer_size,
        workspace=workspace,
        label=label,
    )
    record_key_bytes = record_keys.stat().st_size
    record_keys.unlink()
    record_multiset.unlink()
    if metrics["records"] != record_metrics.eligible_records:
        raise OracleCheckError(
            "eligible record count changed during tagged canonicalization"
        )
    if metrics["records"] == 0:
        raise OracleCheckError("tagged output has no eligible mapped records")
    metrics.update(
        {
            "input_records": record_metrics.input_records,
            "eligible_records": record_metrics.eligible_records,
            "excluded_unmapped": record_metrics.excluded_unmapped,
            "excluded_second_of_pair": record_metrics.excluded_second_of_pair,
            "excluded_unpaired": record_metrics.excluded_unpaired,
            "excluded_mate_unmapped": record_metrics.excluded_mate_unmapped,
            "excluded_chimeric": record_metrics.excluded_chimeric,
            "record_key_bytes": record_key_bytes,
            "reference_sequences": len(header.reference_records),
            "reference_dictionary_sha256": (
                header.reference_dictionary_sha256
            ),
            "read_groups": len(header.read_group_records),
            "read_group_dictionary_sha256": (
                header.read_group_dictionary_sha256
            ),
        }
    )
    return header, metrics, membership, rooted


def compare(
    source: Path,
    canonical_upstream: Path,
    dumi_off: Path,
    *,
    checker,
    samtools: str,
    sort_command: str,
    sort_buffer_size: str,
    temporary_parent: Path | None,
    mode: str,
    remove_unpaired: bool,
    remove_chimeric: bool,
    separator: bytes,
    umi_length: int,
    percentage: float,
    edit_distance: int,
    helper_path: Path,
    checker_path: Path,
) -> dict[str, object]:
    identities = {
        "source": checker.input_identity(source, "source"),
        "canonical_upstream": checker.input_identity(
            canonical_upstream,
            "canonical upstream",
        ),
        "dumi_off": checker.input_identity(dumi_off, "dUMI off"),
    }
    identity_values = {
        (identity.device, identity.inode) for identity in identities.values()
    }
    if len(identity_values) != len(identities):
        raise OracleCheckError(
            "source and tagged inputs must be distinct regular files"
        )
    initial_hashes = {
        "source": sha256_file(source),
        "canonical_upstream": sha256_file(canonical_upstream),
        "dumi_off": sha256_file(dumi_off),
    }

    with checker.private_workspace(temporary_parent) as workspace:
        source_record_keys = workspace / "source.record-keys.sorted"
        oracle_membership = workspace / "oracle.membership.sorted"
        oracle_rooted = workspace / "oracle.rooted.sorted"
        oracle_record_multiset = workspace / "oracle.record-multiset.sorted"
        source_header, source_record_metrics = extract_source_record_keys(
            source,
            source_record_keys,
            checker=checker,
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
        oracle_metrics = canonicalize_source_oracle(
            source_record_keys,
            oracle_membership,
            oracle_rooted,
            oracle_record_multiset,
            checker=checker,
            sort_command=sort_command,
            sort_buffer_size=sort_buffer_size,
            workspace=workspace,
            percentage=percentage,
        )
        source_record_key_bytes = source_record_keys.stat().st_size
        source_record_keys.unlink()
        oracle_record_multiset.unlink()
        if oracle_metrics["records"] != source_record_metrics.eligible_records:
            raise OracleCheckError(
                "eligible source record count changed during oracle construction"
            )
        oracle_metrics.update(
            {
                "input_records": source_record_metrics.input_records,
                "eligible_records": source_record_metrics.eligible_records,
                "excluded_unmapped": source_record_metrics.excluded_unmapped,
                "excluded_second_of_pair": (
                    source_record_metrics.excluded_second_of_pair
                ),
                "excluded_unpaired": source_record_metrics.excluded_unpaired,
                "excluded_mate_unmapped": (
                    source_record_metrics.excluded_mate_unmapped
                ),
                "excluded_chimeric": source_record_metrics.excluded_chimeric,
                "record_key_bytes": source_record_key_bytes,
                "reference_sequences": len(source_header.reference_records),
                "reference_dictionary_sha256": (
                    source_header.reference_dictionary_sha256
                ),
                "read_groups": len(source_header.read_group_records),
                "read_group_dictionary_sha256": (
                    source_header.read_group_dictionary_sha256
                ),
            }
        )

        upstream_header, upstream_metrics, upstream_membership, upstream_rooted = (
            process_tagged(
                canonical_upstream,
                checker=checker,
                label="canonical-upstream",
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
        )
        source_record_multiset = (
            oracle_metrics["alignment_umi_frequency_multiset_bytes"],
            oracle_metrics["alignment_umi_frequency_multiset_sha256"],
        )
        upstream_record_multiset = (
            upstream_metrics["alignment_umi_frequency_multiset_bytes"],
            upstream_metrics["alignment_umi_frequency_multiset_sha256"],
        )
        if upstream_record_multiset != source_record_multiset:
            raise OracleCheckError(
                "canonical upstream changed the eligible alignment/UMI record multiset"
            )
        dumi_header, dumi_metrics, dumi_membership, dumi_rooted = process_tagged(
            dumi_off,
            checker=checker,
            label="dumi-off",
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
        dumi_record_multiset = (
            dumi_metrics["alignment_umi_frequency_multiset_bytes"],
            dumi_metrics["alignment_umi_frequency_multiset_sha256"],
        )
        if dumi_record_multiset != source_record_multiset:
            raise OracleCheckError(
                "dUMI off changed the eligible alignment/UMI record multiset"
            )
        if upstream_header.reference_records != source_header.reference_records:
            raise OracleCheckError(
                "canonical upstream changed the ordered reference dictionary"
            )
        if upstream_header.read_group_records != source_header.read_group_records:
            raise OracleCheckError(
                "canonical upstream changed the normalized read-group dictionary"
            )

        gate = {
            "directional_oracle_gate_pass": False,
            "dumi_off_oracle_partition_equivalent": checker.files_equal(
                dumi_membership,
                oracle_membership,
            ),
            "dumi_off_oracle_root_assignment_equivalent": checker.files_equal(
                dumi_rooted,
                oracle_rooted,
            ),
            "dumi_off_source_reference_dictionary_equivalent": (
                dumi_header.reference_records == source_header.reference_records
            ),
            "dumi_off_source_read_group_dictionary_equivalent": (
                dumi_header.read_group_records == source_header.read_group_records
            ),
        }
        gate["directional_oracle_gate_pass"] = all(
            value
            for key, value in gate.items()
            if key != "directional_oracle_gate_pass"
        )
        diagnostics = {
            "canonical_upstream_oracle_partition_equivalent": checker.files_equal(
                upstream_membership,
                oracle_membership,
            ),
            "canonical_upstream_oracle_root_assignment_equivalent": (
                checker.files_equal(upstream_rooted, oracle_rooted)
            ),
            "canonical_upstream_dumi_off_partition_equivalent": (
                checker.files_equal(upstream_membership, dumi_membership)
            ),
            "canonical_upstream_dumi_off_root_assignment_equivalent": (
                checker.files_equal(upstream_rooted, dumi_rooted)
            ),
            "canonical_upstream_source_reference_dictionary_equivalent": (
                upstream_header.reference_records
                == source_header.reference_records
            ),
            "canonical_upstream_source_read_group_dictionary_equivalent": (
                upstream_header.read_group_records
                == source_header.read_group_records
            ),
        }

        checker.validate_unchanged(source, identities["source"], "source")
        checker.validate_unchanged(
            canonical_upstream,
            identities["canonical_upstream"],
            "canonical upstream",
        )
        checker.validate_unchanged(dumi_off, identities["dumi_off"], "dUMI off")
        final_hashes = {
            "source": sha256_file(source),
            "canonical_upstream": sha256_file(canonical_upstream),
            "dumi_off": sha256_file(dumi_off),
        }
        if final_hashes != initial_hashes:
            raise OracleCheckError(
                "one or more inputs changed cryptographically during comparison"
            )

        oracle_partition_bytes = (
            int(oracle_metrics["membership_partition_bytes"])
            + int(oracle_metrics["rooted_partition_bytes"])
        )
        oracle_record_multiset_bytes = int(
            oracle_metrics["alignment_umi_frequency_multiset_bytes"]
        )
        upstream_partition_bytes = (
            int(upstream_metrics["membership_partition_bytes"])
            + int(upstream_metrics["rooted_partition_bytes"])
        )
        dumi_partition_bytes = (
            int(dumi_metrics["membership_partition_bytes"])
            + int(dumi_metrics["rooted_partition_bytes"])
        )
        peak_persistent = max(
            source_record_key_bytes
            + oracle_partition_bytes
            + oracle_record_multiset_bytes,
            oracle_partition_bytes
            + int(upstream_metrics["record_key_bytes"])
            + upstream_partition_bytes
            + int(
                upstream_metrics[
                    "alignment_umi_frequency_multiset_bytes"
                ]
            ),
            oracle_partition_bytes
            + upstream_partition_bytes
            + int(dumi_metrics["record_key_bytes"])
            + dumi_partition_bytes
            + int(
                dumi_metrics["alignment_umi_frequency_multiset_bytes"]
            ),
        )

    return {
        "schema": SCHEMA,
        "version": SCHEMA_VERSION,
        "methods": {
            "membership_oracle": ORACLE_VERSION,
            "root_total_order": ROOT_ORDER_VERSION,
            "threshold": THRESHOLD_VERSION,
            "membership_partition": MEMBERSHIP_PARTITION_VERSION,
            "rooted_partition": ROOTED_PARTITION_VERSION,
        },
        "configuration": {
            "mode": mode,
            "umi_length": umi_length,
            "umi_separator_bytes": len(separator),
            "umi_separator_sha256": hashlib.sha256(separator).hexdigest(),
            "edit_distance": edit_distance,
            "percentage_decimal": format(percentage, ".17g"),
            "percentage_binary32_hex": struct.pack(
                ">f",
                binary32(percentage),
            ).hex(),
            "remove_unpaired": remove_unpaired,
            "remove_chimeric": remove_chimeric,
            "sort_buffer_size": sort_buffer_size,
        },
        "gate": gate,
        "diagnostics": diagnostics,
        "source_oracle": {
            "input_bytes": identities["source"].size,
            "input_sha256": initial_hashes["source"],
            **oracle_metrics,
        },
        "canonical_upstream": {
            "input_bytes": identities["canonical_upstream"].size,
            "input_sha256": initial_hashes["canonical_upstream"],
            **upstream_metrics,
        },
        "dumi_off": {
            "input_bytes": identities["dumi_off"].size,
            "input_sha256": initial_hashes["dumi_off"],
            **dumi_metrics,
        },
        "temporary_storage": {
            "persistent_stage_peak_upper_bound_bytes": peak_persistent,
            "sort_merge_storage_note": (
                "bounded external-sort merge files are additional and scale "
                "linearly with the active stream"
            ),
        },
        "provenance": {
            "helper_sha256": sha256_file(helper_path),
            "partition_checker_sha256": sha256_file(checker_path),
            "private_streams_retained": False,
        },
    }


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Adjudicate canonical upstream and dUMI tagged Directional clusters "
            "against an independent source-BAM oracle."
        )
    )
    parser.add_argument("source", help="private pre-deduplication SAM/BAM")
    parser.add_argument(
        "canonical_upstream",
        help="private canonical-upstream --tag SAM/BAM",
    )
    parser.add_argument(
        "dumi_off",
        help="private dUMI --streaming-mode off --tag SAM/BAM",
    )
    parser.add_argument(
        "--receipt",
        required=True,
        help="new JSON receipt path; existing destinations are never overwritten",
    )
    parser.add_argument("--umi-length", required=True, type=int)
    parser.add_argument("--umi-separator", default="_")
    parser.add_argument(
        "--mode",
        choices=("single-end", "paired"),
        default="single-end",
    )
    parser.add_argument("--remove-unpaired", action="store_true")
    parser.add_argument("--remove-chimeric", action="store_true")
    parser.add_argument("--tmpdir", "--temp-dir", dest="tmpdir")
    parser.add_argument("--samtools", default="samtools")
    parser.add_argument("--sort-command", default=None)
    parser.add_argument("--sort-buffer-size", default="256M")
    parser.add_argument("--edit-distance", type=int, default=1)
    parser.add_argument(
        "--percentage",
        default="0.5",
        help="Directional percentage; v1 is intentionally fixed at exact 0.5",
    )
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    checker_path = Path(__file__).resolve().with_name(
        "cluster_partition_check.py"
    )
    try:
        checker = load_partition_checker(checker_path)
        parsed = parse_arguments(arguments)
        if parsed.umi_length <= 0:
            raise OracleCheckError("--umi-length must be positive")
        if parsed.edit_distance != 1:
            raise OracleCheckError(
                "this oracle currently requires --edit-distance 1"
            )
        if parsed.edit_distance >= parsed.umi_length:
            raise OracleCheckError(
                "--edit-distance must be smaller than --umi-length"
            )
        try:
            percentage_decimal = Decimal(parsed.percentage)
        except InvalidOperation as error:
            raise OracleCheckError(
                "--percentage must be the exact decimal value 0.5"
            ) from error
        if not percentage_decimal.is_finite() or percentage_decimal != Decimal(
            "0.5"
        ):
            raise OracleCheckError(
                "this oracle version requires --percentage 0.5"
            )
        if not parsed.umi_separator:
            raise OracleCheckError("--umi-separator must not be empty")
        try:
            separator = parsed.umi_separator.encode("ascii")
        except UnicodeEncodeError as error:
            raise OracleCheckError("--umi-separator must be ASCII") from error
        if b"\t" in separator or b"\r" in separator or b"\n" in separator:
            raise OracleCheckError(
                "--umi-separator contains a forbidden character"
            )
        if not checker.SORT_BUFFER_PATTERN.fullmatch(parsed.sort_buffer_size):
            raise OracleCheckError(
                "--sort-buffer-size has an invalid format"
            )
        if parsed.mode != "paired" and (
            parsed.remove_unpaired or parsed.remove_chimeric
        ):
            raise OracleCheckError(
                "paired eligibility flags require --mode paired"
            )

        source = Path(parsed.source).resolve()
        canonical_upstream = Path(parsed.canonical_upstream).resolve()
        dumi_off = Path(parsed.dumi_off).resolve()
        receipt = Path(parsed.receipt).absolute()
        temporary_parent = (
            Path(parsed.tmpdir).resolve() if parsed.tmpdir else None
        )
        samtools = checker.command_path(parsed.samtools, "samtools")
        sort_command = checker.resolve_gnu_sort(parsed.sort_command)
        result = compare(
            source,
            canonical_upstream,
            dumi_off,
            checker=checker,
            samtools=samtools,
            sort_command=sort_command,
            sort_buffer_size=parsed.sort_buffer_size,
            temporary_parent=temporary_parent,
            mode=parsed.mode,
            remove_unpaired=parsed.remove_unpaired,
            remove_chimeric=parsed.remove_chimeric,
            separator=separator,
            umi_length=parsed.umi_length,
            percentage=0.5,
            edit_distance=parsed.edit_distance,
            helper_path=Path(__file__).resolve(),
            checker_path=checker_path,
        )
        checker.atomic_write_json_noreplace(receipt, result)
        return 0 if result["gate"]["directional_oracle_gate_pass"] else 1
    except OracleCheckError as error:
        print(f"directional oracle check error: {error}", file=sys.stderr)
        return 2
    except (OSError, ValueError, OverflowError):
        print(
            "directional oracle check error: private operational I/O failed",
            file=sys.stderr,
        )
        return 2
    except Exception as error:
        if "checker" in locals() and isinstance(
            error,
            checker.PartitionCheckError,
        ):
            print(f"directional oracle check error: {error}", file=sys.stderr)
            return 2
        raise


def cli_entrypoint() -> int:
    checker_path = Path(__file__).resolve().with_name(
        "cluster_partition_check.py"
    )
    checker = load_partition_checker(checker_path)
    checker.install_termination_signal_handlers()
    try:
        return main()
    except checker.PartitionSignalInterrupt as error:
        signal_name = signal.Signals(error.signal_number).name
        print(
            f"directional oracle check error: interrupted by {signal_name}",
            file=sys.stderr,
        )
        return 128 + error.signal_number


if __name__ == "__main__":
    os.umask(0o077)
    raise SystemExit(cli_entrypoint())
