#!/usr/bin/env python3
"""Generate deterministic, coordinate-sorted SAM benchmark workloads.

The generator intentionally uses only the Python standard library.  It writes
small defaults so an accidental invocation is inexpensive; benchmark runners
are expected to pass their chosen scale explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, TextIO, Tuple


GENERATOR_VERSION = "1.0.0"
METADATA_SCHEMA_VERSION = 1
DEFAULT_SEED = 1729
MAX_SAM_COORDINATE = (1 << 31) - 1
MAX_UNIQUE_SCORE = 60
UMI_LENGTH = 12
DNA = "ACGT"
READ_SEQUENCE = "A" * 50
SPARSE_DEFAULT_RECORDS = 10_000
MODERATE_DEFAULT_GROUPS = 256
MODERATE_DEFAULT_FAMILIES = 16
HOTSPOT_DEFAULT_FAMILIES = 4_096
DEFAULT_CHILDREN = 3
DEFAULT_PARENT_COPIES = 8
DEFAULT_CHILD_COPIES = 2
PAIRED_DEFAULT_REFERENCES = 100
PAIRED_DEFAULT_PAIRS_PER_REFERENCE = 1
MASK64 = (1 << 64) - 1


# These are the non-systematic columns of a parity-check matrix over GF(4).
# Together with the four identity columns, every set of three columns is
# independent.  The resulting systematic [12, 8, >=4] quaternary code supplies
# 65,536 family parents separated by at least four nucleotide substitutions.
# One-edit children from different parents are therefore never distance one.
_PARITY_COLUMNS: Tuple[Tuple[int, int, int, int], ...] = (
    (1, 1, 1, 0),
    (1, 3, 0, 2),
    (1, 3, 1, 3),
    (1, 0, 2, 2),
    (1, 1, 3, 1),
    (0, 1, 3, 3),
    (1, 1, 2, 3),
    (1, 2, 2, 1),
)


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected an integer, observed {value!r}") from exc

    if parsed <= 0:
        raise argparse.ArgumentTypeError(f"expected a positive integer, observed {parsed}")

    return parsed


def seed_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected an integer, observed {value!r}") from exc

    if parsed < 0 or parsed > MASK64:
        raise argparse.ArgumentTypeError(
            f"seed must be between 0 and {MASK64}, observed {parsed}"
        )

    return parsed


def _gf4_multiply(left: int, right: int) -> int:
    """Multiply two two-bit GF(4) values using x^2 + x + 1."""

    result = 0

    while right:
        if right & 1:
            result ^= left

        right >>= 1
        left <<= 1

        if left & 0b100:
            left ^= 0b111

    return result


def _splitmix64(value: int) -> int:
    value = (value + 0x9E3779B97F4A7C15) & MASK64
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & MASK64
    return value ^ (value >> 31)


def _affine_code(index: int, bits: int, seed: int, salt: int) -> int:
    """Return a seed-selected permutation of ``index`` modulo 2**bits."""

    mask = (1 << bits) - 1
    state = _splitmix64((seed + salt * 0x9E3779B97F4A7C15) & MASK64)
    multiplier = (state & mask) | 1
    offset = _splitmix64(state) & mask
    return (index * multiplier + offset) & mask


def _base4_symbols(code: int, length: int) -> List[int]:
    symbols = [0] * length

    for index in range(length - 1, -1, -1):
        symbols[index] = code & 0b11
        code >>= 2

    return symbols


def _symbols_to_umi(symbols: Iterable[int]) -> str:
    return "".join(DNA[symbol] for symbol in symbols)


def _ordinary_umi(index: int, seed: int, salt: int) -> str:
    code = _affine_code(index, 2 * UMI_LENGTH, seed, salt)
    return _symbols_to_umi(_base4_symbols(code, UMI_LENGTH))


def _family_parent(family_index: int, seed: int) -> Tuple[int, str]:
    family_code = _affine_code(family_index, 16, seed, 41)
    data = _base4_symbols(family_code, 8)
    parity = [0, 0, 0, 0]

    for symbol, column in zip(data, _PARITY_COLUMNS):
        for parity_index, coefficient in enumerate(column):
            parity[parity_index] ^= _gf4_multiply(symbol, coefficient)

    return family_code, _symbols_to_umi(parity + data)


def _family_child(parent: str, family_code: int, child_index: int) -> str:
    symbols = [DNA.index(base) for base in parent]
    position = (family_code + child_index * 5) % UMI_LENGTH
    delta = 1 + ((family_code >> (2 * (child_index % 8))) + child_index) % 3
    symbols[position] = (symbols[position] + delta) % len(DNA)
    return _symbols_to_umi(symbols)


def _quality(score: int) -> str:
    # SAM quality characters are Phred+33.  Scores are deliberately unique
    # within each exact-UMI duplicate set and agree with MAPQ ranking.
    return chr(33 + score) * len(READ_SEQUENCE)


def _single_record(qname: str, reference: str, position: int, score: int) -> str:
    return (
        f"{qname}\t0\t{reference}\t{position}\t{score}\t50M\t*\t0\t0"
        f"\t{READ_SEQUENCE}\t{_quality(score)}"
    )


def _paired_records(
    qname: str,
    reference: str,
    forward_position: int,
) -> Tuple[str, str]:
    reverse_position = forward_position + 50
    forward = (
        f"{qname}\t99\t{reference}\t{forward_position}\t60\t50M\t="
        f"\t{reverse_position}\t100\t{READ_SEQUENCE}\t{'I' * 50}"
    )
    reverse = (
        f"{qname}\t147\t{reference}\t{reverse_position}\t60\t50M\t="
        f"\t{forward_position}\t-100\t{'T' * 50}\t{'I' * 50}"
    )
    return forward, reverse


class SamWriter:
    def __init__(self, handle: TextIO) -> None:
        self.handle = handle
        self.sha256 = hashlib.sha256()
        self.reference_dictionary_sha256 = hashlib.sha256()
        self.reference_sequence_count = 0
        self.record_count = 0

    def line(self, value: str, *, record: bool = False) -> None:
        encoded = (value + "\n").encode("ascii")
        self.handle.write(encoded.decode("ascii"))
        self.sha256.update(encoded)

        if value.startswith("@SQ\t"):
            self.reference_dictionary_sha256.update(encoded)
            self.reference_sequence_count += 1

        if record:
            self.record_count += 1


class CanonicalRecordDigest:
    """Digest byte-sorted expected non-header SAM records without buffering."""

    def __init__(self) -> None:
        self.sha256 = hashlib.sha256()
        self.record_count = 0
        self._previous: Optional[bytes] = None

    def add(self, record: str) -> None:
        encoded = record.encode("ascii")

        if self._previous is not None and encoded < self._previous:
            raise AssertionError("internal error: expected records are not byte-sorted")

        self.sha256.update(encoded)
        self.sha256.update(b"\n")
        self.record_count += 1
        self._previous = encoded


def _write_header(writer: SamWriter, references: Sequence[Tuple[str, int]]) -> None:
    writer.line("@HD\tVN:1.6\tSO:coordinate")

    for name, length in references:
        writer.line(f"@SQ\tSN:{name}\tLN:{length}")


def _validate_family_shape(args: argparse.Namespace) -> None:
    if args.children_per_family > UMI_LENGTH:
        raise ValueError(
            f"--children-per-family cannot exceed the {UMI_LENGTH}-base UMI length"
        )

    if args.parent_copies > MAX_UNIQUE_SCORE:
        raise ValueError(
            f"--parent-copies cannot exceed {MAX_UNIQUE_SCORE}; unique MAPQ/quality "
            "scores are required"
        )

    if args.child_copies > MAX_UNIQUE_SCORE:
        raise ValueError(
            f"--child-copies cannot exceed {MAX_UNIQUE_SCORE}; unique MAPQ/quality "
            "scores are required"
        )

    directional_threshold = (args.parent_copies + 1) // 2

    if args.child_copies > directional_threshold:
        raise ValueError(
            "--child-copies must be at most floor((parent-copies + 1) / 2) "
            "for the documented directional-collapse expectation"
        )
    if args.parent_copies <= args.child_copies:
        raise ValueError(
            "--parent-copies must be greater than --child-copies so the "
            "expected directional representative is unambiguous"
        )


def _family_qname(
    prefix: str,
    group_index: Optional[int],
    family_index: int,
    umi_index: int,
    copy_index: int,
    umi: str,
    widths: Mapping[str, int],
) -> str:
    group = (
        ""
        if group_index is None
        else f"_g{group_index:0{widths['group']}d}"
    )
    return (
        f"{prefix}{group}_f{family_index:0{widths['family']}d}"
        f"_u{umi_index:02d}_c{copy_index:02d}_{umi}"
    )


def _write_family(
    writer: SamWriter,
    expected: CanonicalRecordDigest,
    *,
    prefix: str,
    reference: str,
    position: int,
    group_index: Optional[int],
    family_index: int,
    family_code_index: int,
    seed: int,
    children_per_family: int,
    parent_copies: int,
    child_copies: int,
    widths: Mapping[str, int],
) -> None:
    family_code, parent = _family_parent(family_code_index, seed)
    umis = [parent]
    umis.extend(
        _family_child(parent, family_code, child_index)
        for child_index in range(children_per_family)
    )

    for umi_index, umi in enumerate(umis):
        copies = parent_copies if umi_index == 0 else child_copies

        for copy_index in range(1, copies + 1):
            qname = _family_qname(
                prefix,
                group_index,
                family_index,
                umi_index,
                copy_index,
                umi,
                widths,
            )
            writer.line(
                _single_record(qname, reference, position, copy_index),
                record=True,
            )

    representative_qname = _family_qname(
        prefix,
        group_index,
        family_index,
        0,
        parent_copies,
        parent,
        widths,
    )
    expected.add(
        _single_record(
            representative_qname,
            reference,
            position,
            parent_copies,
        )
    )


def generate_sparse(
    writer: SamWriter,
    expected: CanonicalRecordDigest,
    args: argparse.Namespace,
) -> Dict[str, object]:
    reference_length = args.records + 200

    if reference_length > MAX_SAM_COORDINATE:
        raise ValueError("--records is too large for a SAM reference coordinate")

    _write_header(writer, (("chr1", reference_length),))
    width = max(1, len(str(args.records)))

    for record_index in range(1, args.records + 1):
        umi = _ordinary_umi(record_index - 1, args.seed, 11)
        qname = f"sparse_r{record_index:0{width}d}_{umi}"
        record = _single_record(qname, "chr1", record_index + 99, 60)
        writer.line(record, record=True)
        expected.add(record)

    return {
        "records": args.records,
        "seed": args.seed,
        "umi_length": UMI_LENGTH,
    }


def generate_moderate(
    writer: SamWriter,
    expected: CanonicalRecordDigest,
    args: argparse.Namespace,
) -> Dict[str, object]:
    _validate_family_shape(args)

    family_count = args.groups * args.families_per_group
    if family_count > (1 << 16):
        raise ValueError(
            "--groups multiplied by --families-per-group cannot exceed 65,536"
        )

    reference_length = args.groups + 200

    if reference_length > MAX_SAM_COORDINATE:
        raise ValueError("--groups is too large for a SAM reference coordinate")

    _write_header(writer, (("chr1", reference_length),))
    widths = {
        "group": max(1, len(str(args.groups))),
        "family": max(1, len(str(args.families_per_group))),
    }

    for group_index in range(1, args.groups + 1):
        position = group_index + 99

        for family_index in range(1, args.families_per_group + 1):
            family_code_index = (
                (group_index - 1) * args.families_per_group + family_index - 1
            )
            _write_family(
                writer,
                expected,
                prefix="moderate",
                reference="chr1",
                position=position,
                group_index=group_index,
                family_index=family_index,
                family_code_index=family_code_index,
                seed=args.seed,
                children_per_family=args.children_per_family,
                parent_copies=args.parent_copies,
                child_copies=args.child_copies,
                widths=widths,
            )

    return {
        "groups": args.groups,
        "families_per_group": args.families_per_group,
        "children_per_family": args.children_per_family,
        "parent_copies": args.parent_copies,
        "child_copies": args.child_copies,
        "seed": args.seed,
        "umi_length": UMI_LENGTH,
    }


def generate_hotspot(
    writer: SamWriter,
    expected: CanonicalRecordDigest,
    args: argparse.Namespace,
) -> Dict[str, object]:
    _validate_family_shape(args)

    if args.families > (1 << 16):
        raise ValueError("--families cannot exceed 65,536")

    _write_header(writer, (("chr1", 1_000),))
    widths = {
        "group": 1,
        "family": max(1, len(str(args.families))),
    }

    for family_index in range(1, args.families + 1):
        _write_family(
            writer,
            expected,
            prefix="hotspot",
            reference="chr1",
            position=100,
            group_index=None,
            family_index=family_index,
            family_code_index=family_index - 1,
            seed=args.seed,
            children_per_family=args.children_per_family,
            parent_copies=args.parent_copies,
            child_copies=args.child_copies,
            widths=widths,
        )

    return {
        "families": args.families,
        "children_per_family": args.children_per_family,
        "parent_copies": args.parent_copies,
        "child_copies": args.child_copies,
        "seed": args.seed,
        "umi_length": UMI_LENGTH,
    }


def generate_paired(
    writer: SamWriter,
    expected: CanonicalRecordDigest,
    args: argparse.Namespace,
) -> Dict[str, object]:
    reference_length = args.pairs_per_reference * 100 + 200

    if reference_length > MAX_SAM_COORDINATE:
        raise ValueError("--pairs-per-reference is too large for a SAM reference coordinate")

    reference_width = max(4, len(str(args.references)))
    pair_width = max(1, len(str(args.pairs_per_reference)))
    references = [
        (f"ref{reference_index:0{reference_width}d}", reference_length)
        for reference_index in range(1, args.references + 1)
    ]
    _write_header(writer, references)

    for reference_index, (reference, _) in enumerate(references, start=1):
        for pair_index in range(1, args.pairs_per_reference + 1):
            global_pair_index = (
                (reference_index - 1) * args.pairs_per_reference + pair_index - 1
            )
            umi = _ordinary_umi(global_pair_index, args.seed, 73)
            qname = (
                f"paired_r{reference_index:0{reference_width}d}"
                f"_p{pair_index:0{pair_width}d}_{umi}"
            )
            forward, reverse = _paired_records(
                qname,
                reference,
                100 + (pair_index - 1) * 100,
            )
            writer.line(forward, record=True)
            writer.line(reverse, record=True)

            # Byte sorting places flag 147 before flag 99 for the shared QNAME.
            expected.add(reverse)
            expected.add(forward)

    return {
        "references": args.references,
        "pairs_per_reference": args.pairs_per_reference,
        "seed": args.seed,
        "umi_length": UMI_LENGTH,
    }


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", required=True, help="coordinate-sorted SAM output path")
    parser.add_argument(
        "--metadata",
        help="optional JSON metadata receipt path; a compact receipt is always written to stderr",
    )
    parser.add_argument("--seed", type=seed_int, default=DEFAULT_SEED)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate deterministic SAM workloads for upstream/dUMI benchmarks."
    )
    subparsers = parser.add_subparsers(dest="workload", required=True)

    sparse = subparsers.add_parser(
        "sparse",
        help="one singleton UMI at each coordinate",
    )
    _add_common_options(sparse)
    sparse.add_argument("--records", type=positive_int, default=SPARSE_DEFAULT_RECORDS)
    sparse.set_defaults(generator=generate_sparse)

    moderate = subparsers.add_parser(
        "moderate",
        help="many moderate coordinate groups containing error-connected UMI families",
    )
    _add_common_options(moderate)
    moderate.add_argument(
        "--groups",
        type=positive_int,
        default=MODERATE_DEFAULT_GROUPS,
    )
    moderate.add_argument(
        "--families-per-group",
        type=positive_int,
        default=MODERATE_DEFAULT_FAMILIES,
    )
    moderate.add_argument(
        "--children-per-family",
        type=positive_int,
        default=DEFAULT_CHILDREN,
    )
    moderate.add_argument(
        "--parent-copies",
        type=positive_int,
        default=DEFAULT_PARENT_COPIES,
    )
    moderate.add_argument(
        "--child-copies",
        type=positive_int,
        default=DEFAULT_CHILD_COPIES,
    )
    moderate.set_defaults(generator=generate_moderate)

    hotspot = subparsers.add_parser(
        "hotspot",
        help="many error-connected UMI families concentrated at one coordinate",
    )
    _add_common_options(hotspot)
    hotspot.add_argument(
        "--families",
        type=positive_int,
        default=HOTSPOT_DEFAULT_FAMILIES,
    )
    hotspot.add_argument(
        "--children-per-family",
        type=positive_int,
        default=DEFAULT_CHILDREN,
    )
    hotspot.add_argument(
        "--parent-copies",
        type=positive_int,
        default=DEFAULT_PARENT_COPIES,
    )
    hotspot.add_argument(
        "--child-copies",
        type=positive_int,
        default=DEFAULT_CHILD_COPIES,
    )
    hotspot.set_defaults(generator=generate_hotspot)

    paired = subparsers.add_parser(
        "paired",
        help="coordinate-sorted pairs distributed across a reference-count sweep",
    )
    _add_common_options(paired)
    paired.add_argument(
        "--references",
        type=positive_int,
        default=PAIRED_DEFAULT_REFERENCES,
    )
    paired.add_argument(
        "--pairs-per-reference",
        type=positive_int,
        default=PAIRED_DEFAULT_PAIRS_PER_REFERENCE,
    )
    paired.set_defaults(generator=generate_paired)

    return parser


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )

    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")

        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _public_command(arguments: Sequence[str]) -> str:
    normalized = [Path(arguments[0]).name]
    replace_next: Optional[str] = None
    for argument in arguments[1:]:
        if replace_next is not None:
            normalized.append(replace_next)
            replace_next = None
        elif argument == "--output":
            normalized.append(argument)
            replace_next = "<OUTPUT_SAM>"
        elif argument == "--metadata":
            normalized.append(argument)
            replace_next = "<METADATA_JSON>"
        else:
            normalized.append(argument)
    return shlex.join(normalized)


def _generate_to_path(args: argparse.Namespace) -> Mapping[str, object]:
    output = Path(args.output)

    if args.metadata is not None and output.resolve() == Path(args.metadata).resolve():
        raise ValueError("--output and --metadata must name different files")

    output.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=str(output.parent),
        prefix=f".{output.name}.",
        suffix=".tmp",
        text=True,
    )
    expected = CanonicalRecordDigest()

    try:
        with os.fdopen(file_descriptor, "w", encoding="ascii", newline="\n") as handle:
            writer = SamWriter(handle)
            parameters = args.generator(writer, expected, args)

        os.replace(temporary_name, output)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise

    source = Path(__file__).resolve()
    workload_id_parts = [args.workload]

    for key, value in parameters.items():
        if key != "umi_length":
            workload_id_parts.append(f"{key}-{value}")

    metadata: Dict[str, object] = {
        "schema_version": METADATA_SCHEMA_VERSION,
        "workload_id": "__".join(workload_id_parts),
        "workload": args.workload,
        "parameters": parameters,
        "generator": {
            "version": GENERATOR_VERSION,
            "source": "scripts/benchmark/generate_workload.py",
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "command": _public_command(sys.argv),
            "python": sys.version.split()[0],
        },
        "input": {
            "path": output.name,
            "format": "SAM",
            "sort_order": "coordinate",
            "bytes": output.stat().st_size,
            "records": writer.record_count,
            "sha256": writer.sha256.hexdigest(),
            "reference_sequences": writer.reference_sequence_count,
            "reference_dictionary_sha256": (
                writer.reference_dictionary_sha256.hexdigest()
            ),
        },
        "expected_output": {
            "profile": "directional-k1-p0.5-mapqual-or-avgqual",
            "records": expected.record_count,
            "canonical_record_sha256": expected.sha256.hexdigest(),
            "canonicalization": (
                "SHA-256 of byte-sorted non-header SAM alignment lines, "
                "each terminated by LF"
            ),
        },
    }

    return metadata


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        metadata = _generate_to_path(args)

        if args.metadata is not None:
            _atomic_json(Path(args.metadata), metadata)

        print(json.dumps(metadata, sort_keys=True, separators=(",", ":")), file=sys.stderr)
        return 0
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
