#!/usr/bin/env python3
"""Validate and summarize dUMI public-benchmark run records."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import sys
from typing import Iterable, TextIO


RAW_FIELDS = (
    "run_id",
    "workload",
    "scale",
    "stage",
    "implementation",
    "mode",
    "repetition",
    "order",
    "exit_code",
    "elapsed_s",
    "user_s",
    "system_s",
    "cpu_pct",
    "max_rss_kib",
    "input_sha256",
    "output_records",
    "semantic_sha256",
    "sort_order",
    "output_bytes",
    "output_sha256",
    "reference_sequences",
    "reference_dictionary_sha256",
    "expected_output_records",
    "expected_semantic_sha256",
    "expected_reference_sequences",
    "expected_reference_dictionary_sha256",
    "command_file",
    "stdout_file",
    "stderr_file",
    "output_file",
)

ALIASES = {
    "workload_id": "workload",
    "workload_variant": "scale",
    "phase": "stage",
    "streaming_requested": "mode",
    "repeat_index": "repetition",
    "order_position": "order",
    "elapsed_seconds": "elapsed_s",
    "user_seconds": "user_s",
    "system_seconds": "system_s",
    "semantic_record_sha256": "semantic_sha256",
}

GROUP_FIELDS = ("workload", "scale", "stage", "implementation", "mode")
MEASURED_STAGES = ("raw", "end_to_end_ready")
METRICS = ("elapsed_s", "user_s", "system_s", "cpu_pct", "max_rss_kib")
SUMMARY_PREFIX_FIELDS = (
    *GROUP_FIELDS,
    "attempts",
    "successful_repetitions",
    "failed_repetitions",
    "correctness_status",
    "comparability_status",
    "comparability_issues",
    "input_sha256",
    "output_records",
    "semantic_sha256",
    "sort_order",
    "reference_sequences",
    "reference_dictionary_sha256",
)
SUMMARY_FIELDS = (
    *SUMMARY_PREFIX_FIELDS,
    *(
        f"{metric}_{statistic}"
        for metric in METRICS
        for statistic in ("n", "median", "min", "max", "range", "mad")
    ),
)
DESIGN_FIELDS = (
    "run_id",
    "workload",
    "scale",
    "stage",
    "implementation",
    "mode",
    "repetition",
    "order",
)
CORRECTNESS_FIELDS = (
    *GROUP_FIELDS,
    "correctness_status",
    "directional_oracle_gate_pass",
    "dumi_off_oracle_partition_equivalent",
    "dumi_off_oracle_root_assignment_equivalent",
    "canonical_upstream_oracle_partition_equivalent",
    "canonical_upstream_oracle_root_assignment_equivalent",
    "canonical_upstream_dumi_off_partition_equivalent",
    "canonical_upstream_dumi_off_root_assignment_equivalent",
    "directional_oracle_receipt",
    "issue_count",
    "issues",
)
COMPARISON_METRICS = (
    "elapsed_speedup",
    "elapsed_change_pct",
    "max_rss_reduction_pct",
)
COMPARISON_FIELDS = (
    "workload",
    "scale",
    "stage",
    "baseline_implementation",
    "baseline_mode",
    "implementation",
    "mode",
    "attempted_pairs",
    "successful_pairs",
    "failed_pairs",
    "correctness_status",
    "issues",
    "comparability_status",
    "comparability_issues",
    "noncomparable_pairs",
    *(
        f"{metric}_{statistic}"
        for metric in COMPARISON_METRICS
        for statistic in ("n", "median", "min", "max", "range", "mad")
    ),
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
EXTERNAL_WORKLOAD = "external"
EXTERNAL_CROSS_OUTPUT_COUNT_MATCH = (
    "cross_implementation_output_count_match"
)
EXTERNAL_CROSS_ALIGNMENT_GROUP_OUTPUT_COUNT_MATCH = (
    "cross_implementation_alignment_group_output_count_match"
)
EXTERNAL_DIRECTIONAL_ORACLE_GATE_PASS = "directional_oracle_gate_pass"
EXTERNAL_DUMI_ORACLE_REQUIRED_FIELDS = (
    "dumi_off_oracle_partition_equivalent",
    "dumi_off_oracle_root_assignment_equivalent",
)
EXTERNAL_DIRECTIONAL_DIAGNOSTIC_FIELDS = (
    "canonical_upstream_oracle_partition_equivalent",
    "canonical_upstream_oracle_root_assignment_equivalent",
    "canonical_upstream_dumi_off_partition_equivalent",
    "canonical_upstream_dumi_off_root_assignment_equivalent",
)
EXTERNAL_DIRECTIONAL_ORACLE_RECEIPT = "directional_oracle_receipt"
NONCOMPARABLE_OUTPUT_COUNT_ISSUE = (
    "cross-implementation-output-count-mismatch"
)
EXTERNAL_ORACLE_IMPLEMENTATIONS = {
    "canonical-upstream": "canonical-upstream",
    "dumi": "dumi-off",
}
EXACT_ORACLE_EVIDENCE_FIELDS = (
    "output_records",
    "semantic_sha256",
    "reference_sequences",
    "reference_dictionary_sha256",
    "expected_output_records",
    "expected_semantic_sha256",
    "expected_reference_sequences",
    "expected_reference_dictionary_sha256",
)
EXPECTED_ORACLE_EVIDENCE_FIELDS = (
    "expected_output_records",
    "expected_semantic_sha256",
    "expected_reference_sequences",
    "expected_reference_dictionary_sha256",
)
CROSS_IMPLEMENTATION_EXACT_FIELDS = (
    "expected_output_records",
    "expected_semantic_sha256",
)


class SummaryError(RuntimeError):
    """The result file is structurally invalid."""


@dataclass
class Cell:
    key: tuple[str, ...]
    rows: list[dict[str, str]]
    issues: list[str]


def normalize_header(fieldnames: list[str] | None) -> dict[str, str]:
    if not fieldnames:
        raise SummaryError("input TSV has no header")
    mapping: dict[str, str] = {}
    canonical_seen: set[str] = set()
    for field in fieldnames:
        canonical = ALIASES.get(field, field)
        if canonical in canonical_seen:
            raise SummaryError(
                f"input contains more than one column mapping to {canonical!r}"
            )
        canonical_seen.add(canonical)
        mapping[field] = canonical
    missing = [field for field in RAW_FIELDS if field not in canonical_seen]
    if missing:
        raise SummaryError(f"input TSV is missing columns: {', '.join(missing)}")
    return mapping


def read_rows(path: Path) -> list[dict[str, str]]:
    try:
        stream = path.open("r", encoding="utf-8", newline="")
    except OSError as error:
        raise SummaryError(f"cannot read {path}: {error}") from error
    with stream:
        reader = csv.DictReader(stream, delimiter="\t")
        mapping = normalize_header(reader.fieldnames)
        rows: list[dict[str, str]] = []
        for line_number, original in enumerate(reader, start=2):
            if None in original:
                raise SummaryError(f"{path}:{line_number}: too many TSV fields")
            row = {
                canonical: (original[source] or "").strip()
                for source, canonical in mapping.items()
            }
            row["_line"] = str(line_number)
            rows.append(row)
    if not rows:
        raise SummaryError("input TSV contains no result rows")
    return rows


def read_design(path: Path) -> list[dict[str, str]]:
    try:
        stream = path.open("r", encoding="utf-8", newline="")
    except OSError as error:
        raise SummaryError(f"cannot read design {path}: {error}") from error
    with stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if not reader.fieldnames:
            raise SummaryError("design TSV has no header")
        missing = [field for field in DESIGN_FIELDS if field not in reader.fieldnames]
        if missing:
            raise SummaryError(
                f"design TSV is missing columns: {', '.join(missing)}"
            )
        rows: list[dict[str, str]] = []
        for line_number, original in enumerate(reader, start=2):
            if None in original:
                raise SummaryError(f"{path}:{line_number}: too many TSV fields")
            row = {
                field: (original[field] or "").strip()
                for field in DESIGN_FIELDS
            }
            row["_line"] = str(line_number)
            for field in DESIGN_FIELDS:
                if not row[field]:
                    raise SummaryError(
                        f"{path}:{line_number}: design field {field} is empty"
                    )
            if row["stage"] not in MEASURED_STAGES:
                raise SummaryError(
                    f"{path}:{line_number}: design stage must be one of "
                    + ", ".join(MEASURED_STAGES)
                )
            integer_value(row["repetition"], "repetition", f"design line {line_number}")
            integer_value(row["order"], "order", f"design line {line_number}")
            rows.append(row)
    if not rows:
        raise SummaryError("design TSV contains no scheduled rows")
    keys = [row_key(row, DESIGN_FIELDS) for row in rows]
    if len(keys) != len(set(keys)):
        raise SummaryError("design TSV contains duplicate scheduled tuples")
    run_ids = [row["run_id"] for row in rows]
    if len(run_ids) != len(set(run_ids)):
        raise SummaryError("design TSV contains duplicate run_id values")
    validate_design_schedule(rows)
    return rows


def validate_design_schedule(rows: list[dict[str, str]]) -> None:
    by_round: dict[
        tuple[str, str, str, str],
        list[dict[str, str]],
    ] = defaultdict(list)
    by_scope: dict[
        tuple[str, str, str],
        list[dict[str, str]],
    ] = defaultdict(list)
    by_trial: dict[
        tuple[str, str, str, str, str],
        list[dict[str, str]],
    ] = defaultdict(list)
    for row in rows:
        by_round[
            (row["workload"], row["scale"], row["stage"], row["repetition"])
        ].append(row)
        by_scope[(row["workload"], row["scale"], row["stage"])].append(row)
        by_trial[
            (
                row["workload"],
                row["scale"],
                row["implementation"],
                row["mode"],
                row["repetition"],
            )
        ].append(row)

    for key, round_rows in by_round.items():
        positions = {int(row["order"]) for row in round_rows}
        expected = set(range(1, len(round_rows) + 1))
        if positions != expected:
            raise SummaryError(
                f"design round {'/'.join(key)} order positions are not "
                f"exactly 1..{len(round_rows)}"
            )
        treatments = {
            (row["implementation"], row["mode"])
            for row in round_rows
        }
        if len(treatments) != len(round_rows):
            raise SummaryError(
                f"design round {'/'.join(key)} repeats a treatment"
            )

    for key, trial_rows in by_trial.items():
        if {row["stage"] for row in trial_rows} != set(MEASURED_STAGES):
            raise SummaryError(
                f"design trial {'/'.join(key)} must have one row for every "
                "measured stage"
            )

    for key, scope_rows in by_scope.items():
        by_repetition: dict[str, set[tuple[str, str]]] = defaultdict(set)
        for row in scope_rows:
            by_repetition[row["repetition"]].add(
                (row["implementation"], row["mode"])
            )
        treatment_sets = list(by_repetition.values())
        if any(current != treatment_sets[0] for current in treatment_sets[1:]):
            raise SummaryError(
                f"design scope {'/'.join(key)} changes treatments between repetitions"
            )
        treatments = treatment_sets[0]
        positions = range(1, len(treatments) + 1)
        for treatment in treatments:
            counts = {
                position: sum(
                    1
                    for row in scope_rows
                    if (row["implementation"], row["mode"]) == treatment
                    and int(row["order"]) == position
                )
                for position in positions
            }
            if max(counts.values()) - min(counts.values()) > 1:
                label = "/".join(treatment)
                raise SummaryError(
                    f"design scope {'/'.join(key)} does not balance {label} "
                    "across order positions"
                )
        if len(treatments) % 2 == 0:
            ordered_treatments = sorted(treatments)
            carryover_counts = {
                (left, right): 0
                for left in ordered_treatments
                for right in ordered_treatments
                if left != right
            }
            by_repetition_rows: dict[str, list[dict[str, str]]] = (
                defaultdict(list)
            )
            for row in scope_rows:
                by_repetition_rows[row["repetition"]].append(row)
            for repetition_rows in by_repetition_rows.values():
                sequence = [
                    (row["implementation"], row["mode"])
                    for row in sorted(
                        repetition_rows,
                        key=lambda candidate: int(candidate["order"]),
                    )
                ]
                for left, right in zip(sequence, sequence[1:]):
                    carryover_counts[(left, right)] += 1
            observed = list(carryover_counts.values())
            if max(observed) - min(observed) > 1:
                raise SummaryError(
                    f"design scope {'/'.join(key)} does not balance "
                    "first-order treatment carryover"
                )


def apply_design(
    rows: list[dict[str, str]],
    design: list[dict[str, str]],
) -> list[str]:
    issues: list[str] = []
    measured = {row_key(row, DESIGN_FIELDS): row for row in rows}
    scheduled = {row_key(row, DESIGN_FIELDS): row for row in design}
    missing = sorted(set(scheduled) - set(measured))
    extra = sorted(set(measured) - set(scheduled))
    measured_sequence = [row_key(row, DESIGN_FIELDS) for row in rows]
    scheduled_sequence = [row_key(row, DESIGN_FIELDS) for row in design]

    for key in missing:
        expected = scheduled[key]
        message = "scheduled measurement is missing"
        placeholder = {field: "" for field in RAW_FIELDS}
        placeholder.update(expected)
        placeholder.update(
            {
                "exit_code": "1",
                "_line": f"design line {expected['_line']}",
                "_validation_issues": [message],
            }
        )
        rows.append(placeholder)
        issues.append(f"{'/'.join(key)}: {message}")
    for key in extra:
        message = "measurement is not present in the design"
        measured[key]["_validation_issues"].append(message)
        issues.append(f"{'/'.join(key)}: {message}")
    if not missing and not extra and measured_sequence != scheduled_sequence:
        issues.append("measurement row order does not match the precomputed design")
    return issues


def decimal_value(value: str, field: str, context: str) -> Decimal:
    text = value[:-1] if field == "cpu_pct" and value.endswith("%") else value
    try:
        number = Decimal(text)
    except InvalidOperation as error:
        raise SummaryError(f"{context}: {field} is not numeric: {value!r}") from error
    if not number.is_finite() or number < 0:
        raise SummaryError(f"{context}: {field} must be finite and nonnegative")
    return number


def integer_value(value: str, field: str, context: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise SummaryError(f"{context}: {field} is not an integer: {value!r}") from error
    if number < 0:
        raise SummaryError(f"{context}: {field} must be nonnegative")
    return number


def validate_rows(rows: list[dict[str, str]]) -> list[str]:
    issues: list[str] = []
    by_run_id: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        row["_validation_issues"] = []
        context = f"line {row['_line']}"

        def add_issue(message: str) -> None:
            issues.append(message)
            row["_validation_issues"].append(message)

        required_text = (
            "run_id",
            "workload",
            "scale",
            "stage",
            "implementation",
            "mode",
            "repetition",
            "order",
            "exit_code",
            "input_sha256",
            "expected_output_records",
            "expected_semantic_sha256",
            "expected_reference_sequences",
            "expected_reference_dictionary_sha256",
        )
        for field in required_text:
            if not row[field]:
                raise SummaryError(f"{context}: {field} is empty")
        if row["stage"] not in MEASURED_STAGES:
            raise SummaryError(
                f"{context}: stage must be one of "
                + ", ".join(MEASURED_STAGES)
            )
        if row["workload"] == EXTERNAL_WORKLOAD:
            if boolean_measurement_value(row, "exact_oracle_match") is not True:
                add_issue(
                    f"{context}: external measurement is missing a true "
                    "exact_oracle_match"
                )
            expected_oracle = EXTERNAL_ORACLE_IMPLEMENTATIONS.get(
                row["implementation"]
            )
            if (
                expected_oracle is None
                or row.get("oracle_implementation", "") != expected_oracle
            ):
                add_issue(
                    f"{context}: external oracle_implementation does not match "
                    f"the {row['implementation']} implementation contract"
                )
            if (
                boolean_measurement_value(
                    row, "cross_implementation_exact_match"
                )
                is None
            ):
                add_issue(
                    f"{context}: external measurement is missing a valid "
                    "boolean cross_implementation_exact_match"
                )
            for field in (
                EXTERNAL_CROSS_OUTPUT_COUNT_MATCH,
                EXTERNAL_CROSS_ALIGNMENT_GROUP_OUTPUT_COUNT_MATCH,
                *EXTERNAL_DIRECTIONAL_DIAGNOSTIC_FIELDS,
            ):
                if boolean_measurement_value(row, field) is None:
                    add_issue(
                        f"{context}: external measurement is missing a valid "
                        f"boolean {field}"
                    )
            for field in (
                EXTERNAL_DIRECTIONAL_ORACLE_GATE_PASS,
                *EXTERNAL_DUMI_ORACLE_REQUIRED_FIELDS,
            ):
                if boolean_measurement_value(row, field) is not True:
                    add_issue(
                        f"{context}: external measurement is missing a true "
                        f"{field}"
                    )
            if not row.get(EXTERNAL_DIRECTIONAL_ORACLE_RECEIPT, "").strip():
                add_issue(
                    f"{context}: external measurement is missing a nonempty "
                    f"{EXTERNAL_DIRECTIONAL_ORACLE_RECEIPT}"
                )
        by_run_id[row["run_id"]].append(row)
        integer_value(row["repetition"], "repetition", context)
        integer_value(row["order"], "order", context)
        exit_code = integer_value(row["exit_code"], "exit_code", context)
        for metric in METRICS:
            if row[metric]:
                decimal_value(row[metric], metric, context)
            elif exit_code == 0:
                add_issue(f"{context}: successful run has empty {metric}")
        if not SHA256.fullmatch(row["input_sha256"]):
            add_issue(f"{context}: invalid input_sha256")
        for field in (
            "expected_semantic_sha256",
            "expected_reference_dictionary_sha256",
        ):
            if not SHA256.fullmatch(row[field]):
                add_issue(f"{context}: invalid {field}")
        for field in ("expected_output_records", "expected_reference_sequences"):
            try:
                integer_value(row[field], field, context)
            except SummaryError as error:
                add_issue(str(error))
        if exit_code != 0:
            add_issue(f"{context}: process exited {exit_code}")
            continue
        if not row["output_records"]:
            add_issue(f"{context}: successful run has no output_records")
        else:
            try:
                integer_value(row["output_records"], "output_records", context)
            except SummaryError as error:
                add_issue(str(error))
        if not SHA256.fullmatch(row["semantic_sha256"]):
            add_issue(f"{context}: invalid semantic_sha256")
        if not row["sort_order"]:
            add_issue(f"{context}: successful run has no sort_order")
        if not row["output_bytes"]:
            add_issue(f"{context}: successful run has no output_bytes")
        else:
            try:
                integer_value(row["output_bytes"], "output_bytes", context)
            except SummaryError as error:
                add_issue(str(error))
        if not SHA256.fullmatch(row["output_sha256"]):
            add_issue(f"{context}: invalid output_sha256")
        if not row["reference_sequences"]:
            add_issue(f"{context}: successful run has no reference_sequences")
        else:
            try:
                integer_value(row["reference_sequences"], "reference_sequences", context)
            except SummaryError as error:
                add_issue(str(error))
        if not SHA256.fullmatch(row["reference_dictionary_sha256"]):
            add_issue(f"{context}: invalid reference_dictionary_sha256")
        for observed, expected in (
            ("output_records", "expected_output_records"),
            ("semantic_sha256", "expected_semantic_sha256"),
            ("reference_sequences", "expected_reference_sequences"),
            (
                "reference_dictionary_sha256",
                "expected_reference_dictionary_sha256",
            ),
        ):
            if row[observed] and row[expected] and row[observed] != row[expected]:
                add_issue(
                    f"{context}: {observed} does not match recorded workload "
                    f"oracle {expected}"
                )
    for run_id, matching_rows in by_run_id.items():
        if len(matching_rows) < 2:
            continue
        for row in matching_rows:
            message = f"duplicate run_id {run_id!r}"
            issues.append(f"line {row['_line']}: {message}")
            row["_validation_issues"].append(message)
    return issues


def row_key(row: dict[str, str], fields: Iterable[str]) -> tuple[str, ...]:
    return tuple(row[field] for field in fields)


def external_performance_is_comparable(row: dict[str, str]) -> bool:
    return boolean_measurement_value(
        row, EXTERNAL_CROSS_OUTPUT_COUNT_MATCH
    ) is True


def boolean_measurement_value(
    row: dict[str, str], field: str
) -> bool | None:
    text = row.get(field, "").strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    return None


def median(numbers: list[Decimal]) -> Decimal:
    ordered = sorted(numbers)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def statistics(numbers: list[Decimal]) -> dict[str, Decimal]:
    center = median(numbers)
    minimum = min(numbers)
    maximum = max(numbers)
    return {
        "median": center,
        "min": minimum,
        "max": maximum,
        "range": maximum - minimum,
        "mad": median([abs(number - center) for number in numbers]),
    }


def format_decimal(number: Decimal) -> str:
    if not number.is_finite():
        raise SummaryError("cannot format a non-finite number")
    if number == number.to_integral():
        return str(number.quantize(Decimal(1)))
    return format(number.normalize(), "f")


def group_cells(rows: list[dict[str, str]]) -> dict[tuple[str, ...], Cell]:
    grouped: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row_key(row, GROUP_FIELDS)].append(row)
    return {
        key: Cell(key=key, rows=sorted(value, key=lambda row: int(row["repetition"])), issues=[])
        for key, value in grouped.items()
    }


def check_correctness(
    rows: list[dict[str, str]],
    cells: dict[tuple[str, ...], Cell],
    expected_repetitions: int | None,
) -> list[str]:
    issues: list[str] = []
    by_scope: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    by_external_workload: dict[
        tuple[str, str], list[dict[str, str]]
    ] = defaultdict(list)
    for row in rows:
        by_scope[(row["workload"], row["scale"], row["stage"])].append(row)
        if row["workload"] == EXTERNAL_WORKLOAD:
            by_external_workload[(row["workload"], row["scale"])].append(row)
        cell = cells[row_key(row, GROUP_FIELDS)]
        cell.issues.extend(row.get("_validation_issues", []))
        if int(row["exit_code"]) != 0:
            cell.issues.append(
                f"repetition {row['repetition']} exited {row['exit_code']}"
            )
        if row.get("_derived_issue"):
            cell.issues.append(
                f"repetition {row['repetition']}: {row['_derived_issue']}"
            )

    for cell in cells.values():
        repetitions = [row["repetition"] for row in cell.rows]
        if len(repetitions) != len(set(repetitions)):
            cell.issues.append("duplicate repetition index")
        if expected_repetitions is not None and len(cell.rows) != expected_repetitions:
            cell.issues.append(
                f"has {len(cell.rows)} attempts; expected {expected_repetitions}"
            )
        if expected_repetitions is not None and set(repetitions) != {
            str(index) for index in range(1, expected_repetitions + 1)
        }:
            cell.issues.append(
                "repetition indexes are not exactly "
                f"1..{expected_repetitions}"
            )
        successful = [
            row
            for row in cell.rows
            if int(row["exit_code"]) == 0
            and not row.get("_validation_issues")
            and not row.get("_derived_issue")
        ]
        fingerprints = {
            (
                row["output_records"],
                row["semantic_sha256"],
                row["reference_sequences"],
                row["reference_dictionary_sha256"],
            )
            for row in successful
        }
        if len(fingerprints) > 1:
            cell.issues.append("successful repetitions have different record multisets")
        inputs = {row["input_sha256"] for row in cell.rows if row["input_sha256"]}
        if len(inputs) > 1:
            cell.issues.append("repetitions use different input hashes")
        if cell.key[2] == "end_to_end_ready":
            non_coordinate = {
                row["sort_order"]
                for row in successful
                if row["sort_order"] != "coordinate"
            }
            if non_coordinate:
                cell.issues.append(
                    "downstream-ready output is not coordinate sorted: "
                    + ", ".join(sorted(non_coordinate))
                )

    for scope, scope_rows in by_scope.items():
        successful = [
            row
            for row in scope_rows
            if int(row["exit_code"]) == 0
            and not row.get("_validation_issues")
            and not row.get("_derived_issue")
        ]
        inputs = {row["input_sha256"] for row in successful}
        fingerprints = {
            (
                row["output_records"],
                row["semantic_sha256"],
                row["reference_sequences"],
                row["reference_dictionary_sha256"],
            )
            for row in successful
        }
        affected = {
            row_key(row, GROUP_FIELDS)
            for row in successful
        }
        if len(inputs) > 1:
            message = f"{'/'.join(scope)} uses different input hashes across cells"
            issues.append(message)
            for key in affected:
                cells[key].issues.append(message)
        if scope[0] == EXTERNAL_WORKLOAD:
            by_oracle: dict[str, list[dict[str, str]]] = defaultdict(list)
            for row in successful:
                by_oracle[row.get("oracle_implementation", "")].append(row)
            for oracle, oracle_rows in by_oracle.items():
                oracle_fingerprints = {
                    tuple(row[field] for field in EXACT_ORACLE_EVIDENCE_FIELDS)
                    for row in oracle_rows
                }
                if len(oracle_fingerprints) > 1:
                    message = (
                        f"{'/'.join(scope)} has inconsistent exact evidence "
                        f"for external oracle {oracle}"
                    )
                    issues.append(message)
                    for row in oracle_rows:
                        cells[row_key(row, GROUP_FIELDS)].issues.append(message)
            differing_evidence = False
            message = ""
        else:
            differing_evidence = len(fingerprints) > 1
            message = (
                f"{'/'.join(scope)} has non-equivalent record multisets across cells"
            )
        if differing_evidence:
            issues.append(message)
            for key in affected:
                cells[key].issues.append(message)

    for workload, workload_rows in by_external_workload.items():
        consistent_fields = (
            "cross_implementation_exact_match",
            EXTERNAL_CROSS_OUTPUT_COUNT_MATCH,
            EXTERNAL_CROSS_ALIGNMENT_GROUP_OUTPUT_COUNT_MATCH,
            EXTERNAL_DIRECTIONAL_ORACLE_GATE_PASS,
            *EXTERNAL_DUMI_ORACLE_REQUIRED_FIELDS,
            *EXTERNAL_DIRECTIONAL_DIAGNOSTIC_FIELDS,
            EXTERNAL_DIRECTIONAL_ORACLE_RECEIPT,
        )
        for field in consistent_fields:
            observed = {
                (
                    row.get(field, "").strip()
                    if field == EXTERNAL_DIRECTIONAL_ORACLE_RECEIPT
                    else boolean_measurement_value(row, field)
                )
                for row in workload_rows
            }
            if len(observed) > 1:
                message = (
                    f"{'/'.join(workload)} has inconsistent {field} metadata"
                )
                issues.append(message)
                for row in workload_rows:
                    cells[row_key(row, GROUP_FIELDS)].issues.append(message)
        rows_by_oracle: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in workload_rows:
            rows_by_oracle[
                row.get("oracle_implementation", "")
            ].append(row)
        for oracle, oracle_rows in rows_by_oracle.items():
            expected_fingerprints = {
                tuple(
                    row[field] for field in EXPECTED_ORACLE_EVIDENCE_FIELDS
                )
                for row in oracle_rows
            }
            if len(expected_fingerprints) > 1:
                message = (
                    f"{'/'.join(workload)} has inconsistent expected "
                    f"evidence for external oracle {oracle}"
                )
                issues.append(message)
                for row in oracle_rows:
                    cells[row_key(row, GROUP_FIELDS)].issues.append(message)
        required_oracles = {"canonical-upstream", "dumi-off"}
        if required_oracles.issubset(rows_by_oracle):
            upstream_exact = {
                tuple(
                    row[field] for field in CROSS_IMPLEMENTATION_EXACT_FIELDS
                )
                for row in rows_by_oracle["canonical-upstream"]
            }
            dumi_exact = {
                tuple(
                    row[field] for field in CROSS_IMPLEMENTATION_EXACT_FIELDS
                )
                for row in rows_by_oracle["dumi-off"]
            }
            reported_values = {
                boolean_measurement_value(
                    row, "cross_implementation_exact_match"
                )
                for row in workload_rows
            }
            if (
                len(upstream_exact) == 1
                and len(dumi_exact) == 1
                and len(reported_values) == 1
                and None not in reported_values
            ):
                observed_exact_match = upstream_exact == dumi_exact
                reported_exact_match = next(iter(reported_values))
                if reported_exact_match != observed_exact_match:
                    message = (
                        f"{'/'.join(workload)} reports "
                        "cross_implementation_exact_match inconsistent with "
                        "the implementation-oracle evidence"
                    )
                    issues.append(message)
                    for row in workload_rows:
                        cells[row_key(row, GROUP_FIELDS)].issues.append(message)
            reported_output_count_values = {
                boolean_measurement_value(
                    row, EXTERNAL_CROSS_OUTPUT_COUNT_MATCH
                )
                for row in workload_rows
            }
            if (
                len(upstream_exact) == 1
                and len(dumi_exact) == 1
                and len(reported_output_count_values) == 1
                and None not in reported_output_count_values
            ):
                observed_output_count_match = {
                    value[0] for value in upstream_exact
                } == {
                    value[0] for value in dumi_exact
                }
                reported_output_count_match = next(
                    iter(reported_output_count_values)
                )
                if (
                    reported_output_count_match
                    != observed_output_count_match
                ):
                    message = (
                        f"{'/'.join(workload)} reports "
                        f"{EXTERNAL_CROSS_OUTPUT_COUNT_MATCH} inconsistent "
                        "with the implementation-oracle evidence"
                    )
                    issues.append(message)
                    for row in workload_rows:
                        cells[row_key(row, GROUP_FIELDS)].issues.append(
                            message
                        )

    for cell in cells.values():
        for issue in cell.issues:
            issues.append(f"{'/'.join(cell.key)}: {issue}")
    return issues


def summarize_cell(cell: Cell) -> dict[str, str]:
    successful = [
        row
        for row in cell.rows
        if int(row["exit_code"]) == 0
        and not row.get("_validation_issues")
        and not row.get("_derived_issue")
    ]
    inputs = sorted({row["input_sha256"] for row in cell.rows if row["input_sha256"]})
    records = sorted({row["output_records"] for row in successful})
    digests = sorted({row["semantic_sha256"] for row in successful})
    sort_orders = sorted({row["sort_order"] for row in successful})
    reference_sequences = sorted(
        {row["reference_sequences"] for row in successful}
    )
    reference_digests = sorted(
        {row["reference_dictionary_sha256"] for row in successful}
    )
    noncomparable = (
        cell.key[0] == EXTERNAL_WORKLOAD
        and any(
            boolean_measurement_value(
                row, EXTERNAL_CROSS_OUTPUT_COUNT_MATCH
            )
            is False
            for row in cell.rows
        )
    )
    summary = dict(zip(GROUP_FIELDS, cell.key))
    summary.update(
        {
            "attempts": str(len(cell.rows)),
            "successful_repetitions": str(len(successful)),
            "failed_repetitions": str(len(cell.rows) - len(successful)),
            "correctness_status": "fail" if cell.issues else "pass",
            "comparability_status": (
                "not_comparable" if noncomparable else "comparable"
            ),
            "comparability_issues": (
                NONCOMPARABLE_OUTPUT_COUNT_ISSUE
                if noncomparable
                else ""
            ),
            "input_sha256": ",".join(inputs),
            "output_records": ",".join(records),
            "semantic_sha256": ",".join(digests),
            "sort_order": ",".join(sort_orders),
            "reference_sequences": ",".join(reference_sequences),
            "reference_dictionary_sha256": ",".join(reference_digests),
        }
    )
    for metric in METRICS:
        values = [
            decimal_value(row[metric], metric, f"run {row['run_id']}")
            for row in successful
            if row[metric]
        ]
        summary[f"{metric}_n"] = str(len(values))
        if values:
            stats = statistics(values)
            for statistic, value in stats.items():
                summary[f"{metric}_{statistic}"] = format_decimal(value)
        else:
            for statistic in ("median", "min", "max", "range", "mad"):
                summary[f"{metric}_{statistic}"] = ""
    return summary


def build_comparisons(
    rows: list[dict[str, str]],
    cells: dict[tuple[str, ...], Cell] | None = None,
) -> tuple[list[dict[str, str]], list[str]]:
    by_scope_repetition: dict[
        tuple[str, str, str, str], list[dict[str, str]]
    ] = defaultdict(list)
    for row in rows:
        by_scope_repetition[
            (
                row["workload"],
                row["scale"],
                row["stage"],
                row["repetition"],
            )
        ].append(row)

    pairs: dict[
        tuple[str, str, str, str, str],
        list[tuple[dict[str, str] | None, dict[str, str]]],
    ] = defaultdict(list)
    external_dumi_oracles: dict[
        tuple[str, str, str, str], dict[str, str] | None
    ] = {}
    pair_setup_issues: dict[tuple[str, str, str, str, str], list[str]] = defaultdict(list)
    for scope, scope_rows in by_scope_repetition.items():
        baselines = [
            row
            for row in scope_rows
            if row["implementation"] == "canonical-upstream"
            and row["mode"] == "legacy"
        ]
        treatments = [
            row
            for row in scope_rows
            if not (
                row["implementation"] == "canonical-upstream"
                and row["mode"] == "legacy"
            )
        ]
        baseline = baselines[0] if len(baselines) == 1 else None
        if scope[0] == EXTERNAL_WORKLOAD:
            dumi_off_rows = [
                row
                for row in scope_rows
                if row["implementation"] == "dumi"
                and row["mode"] == "off"
            ]
            external_dumi_oracles[scope] = (
                dumi_off_rows[0] if len(dumi_off_rows) == 1 else None
            )
        for treatment in treatments:
            key = (
                treatment["workload"],
                treatment["scale"],
                treatment["stage"],
                treatment["implementation"],
                treatment["mode"],
            )
            pairs[key].append((baseline, treatment))
            if len(baselines) != 1:
                pair_setup_issues[key].append(
                    f"repetition {scope[3]} has {len(baselines)} canonical baselines"
                )

    comparison_rows: list[dict[str, str]] = []
    all_issues: list[str] = []
    for key in sorted(pairs):
        pair_values = pairs[key]
        issues = list(pair_setup_issues[key])
        metric_values: dict[str, list[Decimal]] = {
            metric: [] for metric in COMPARISON_METRICS
        }
        successful_pairs = 0
        noncomparable_pairs = 0
        comparability_issues: list[str] = []
        for baseline, treatment in pair_values:
            repetition = treatment["repetition"]
            if baseline is None:
                continue
            baseline_valid = (
                int(baseline["exit_code"]) == 0
                and not baseline.get("_validation_issues")
                and not baseline.get("_derived_issue")
            )
            treatment_valid = (
                int(treatment["exit_code"]) == 0
                and not treatment.get("_validation_issues")
                and not treatment.get("_derived_issue")
            )
            if not baseline_valid or not treatment_valid:
                issues.append(f"repetition {repetition} has an invalid source row")
                continue
            if cells is not None:
                failed_source_cells = [
                    "/".join(row_key(source, GROUP_FIELDS))
                    for source in (baseline, treatment)
                    if cells[row_key(source, GROUP_FIELDS)].issues
                ]
                if failed_source_cells:
                    issues.append(
                        f"repetition {repetition} has failed correctness "
                        "source cells: " + ", ".join(failed_source_cells)
                    )
                    continue
            if treatment["workload"] == EXTERNAL_WORKLOAD:
                oracle_scope = (
                    treatment["workload"],
                    treatment["scale"],
                    treatment["stage"],
                    treatment["repetition"],
                )
                dumi_oracle = external_dumi_oracles.get(oracle_scope)
                if dumi_oracle is None:
                    issues.append(
                        f"repetition {repetition} does not have exactly one "
                        "dumi-off external oracle row"
                    )
                    continue
                dumi_oracle_valid = (
                    int(dumi_oracle["exit_code"]) == 0
                    and not dumi_oracle.get("_validation_issues")
                    and not dumi_oracle.get("_derived_issue")
                )
                if not dumi_oracle_valid:
                    issues.append(
                        f"repetition {repetition} has an invalid dumi-off "
                        "external oracle row"
                    )
                    continue
                if any(
                    treatment[field] != dumi_oracle[field]
                    for field in EXPECTED_ORACLE_EVIDENCE_FIELDS
                ):
                    issues.append(
                        f"repetition {repetition} treatment does not use the "
                        "shared dumi-off external oracle"
                    )
                    continue
                equality_fields = (
                    "input_sha256",
                    EXTERNAL_DIRECTIONAL_ORACLE_GATE_PASS,
                    *EXTERNAL_DUMI_ORACLE_REQUIRED_FIELDS,
                    *EXTERNAL_DIRECTIONAL_DIAGNOSTIC_FIELDS,
                    EXTERNAL_DIRECTIONAL_ORACLE_RECEIPT,
                )
            else:
                equality_fields = (
                    "input_sha256",
                    "output_records",
                    "semantic_sha256",
                    "reference_sequences",
                    "reference_dictionary_sha256",
                    "expected_output_records",
                    "expected_semantic_sha256",
                    "expected_reference_sequences",
                    "expected_reference_dictionary_sha256",
                )
            if any(
                baseline[field] != treatment[field] for field in equality_fields
            ):
                issues.append(
                    f"repetition {repetition} baseline and treatment evidence differ"
                )
                continue

            if (
                treatment["workload"] == EXTERNAL_WORKLOAD
                and not (
                    external_performance_is_comparable(baseline)
                    and external_performance_is_comparable(treatment)
                )
            ):
                noncomparable_pairs += 1
                comparability_issues.append(
                    NONCOMPARABLE_OUTPUT_COUNT_ISSUE
                )
                continue

            baseline_elapsed = decimal_value(
                baseline["elapsed_s"], "elapsed_s", f"comparison {key}"
            )
            treatment_elapsed = decimal_value(
                treatment["elapsed_s"], "elapsed_s", f"comparison {key}"
            )
            baseline_rss = decimal_value(
                baseline["max_rss_kib"], "max_rss_kib", f"comparison {key}"
            )
            treatment_rss = decimal_value(
                treatment["max_rss_kib"], "max_rss_kib", f"comparison {key}"
            )
            if baseline_elapsed <= 0 or treatment_elapsed <= 0:
                issues.append(
                    f"repetition {repetition} has nonpositive elapsed time"
                )
                continue
            if baseline_rss <= 0:
                issues.append(
                    f"repetition {repetition} has nonpositive baseline max RSS"
                )
                continue

            hundred = Decimal(100)
            metric_values["elapsed_speedup"].append(
                baseline_elapsed / treatment_elapsed
            )
            metric_values["elapsed_change_pct"].append(
                (treatment_elapsed - baseline_elapsed)
                * hundred
                / baseline_elapsed
            )
            metric_values["max_rss_reduction_pct"].append(
                (baseline_rss - treatment_rss) * hundred / baseline_rss
            )
            successful_pairs += 1

        unique_issues = list(dict.fromkeys(issues))
        unique_comparability_issues = list(
            dict.fromkeys(comparability_issues)
        )
        row = {
            "workload": key[0],
            "scale": key[1],
            "stage": key[2],
            "baseline_implementation": "canonical-upstream",
            "baseline_mode": "legacy",
            "implementation": key[3],
            "mode": key[4],
            "attempted_pairs": str(len(pair_values)),
            "successful_pairs": str(successful_pairs),
            "failed_pairs": str(
                len(pair_values)
                - successful_pairs
                - noncomparable_pairs
            ),
            "correctness_status": "fail" if unique_issues else "pass",
            "issues": " | ".join(unique_issues),
            "comparability_status": (
                "not_comparable"
                if noncomparable_pairs
                else "comparable"
            ),
            "comparability_issues": " | ".join(
                unique_comparability_issues
            ),
            "noncomparable_pairs": str(noncomparable_pairs),
        }
        for metric in COMPARISON_METRICS:
            values = metric_values[metric]
            row[f"{metric}_n"] = str(len(values))
            if values:
                stats = statistics(values)
                for statistic, value in stats.items():
                    row[f"{metric}_{statistic}"] = format_decimal(value)
            else:
                for statistic in ("median", "min", "max", "range", "mad"):
                    row[f"{metric}_{statistic}"] = ""
        comparison_rows.append(row)
        for issue in unique_issues:
            all_issues.append(
                f"{'/'.join(key)} comparison: {issue}"
            )
    return comparison_rows, all_issues


def output_stream(path: str | None) -> tuple[TextIO, bool]:
    if path is None or path == "-":
        return sys.stdout, False
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination.open("w", encoding="utf-8", newline=""), True


def write_summary(rows: list[dict[str, str]], output: str | None) -> None:
    stream, should_close = output_stream(output)
    try:
        writer = csv.DictWriter(
            stream,
            fieldnames=SUMMARY_FIELDS,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
    finally:
        if should_close:
            stream.close()


def write_correctness(
    cells: dict[tuple[str, ...], Cell],
    output: str | None,
) -> None:
    if output is None:
        return
    stream, should_close = output_stream(output)
    try:
        writer = csv.DictWriter(
            stream,
            fieldnames=CORRECTNESS_FIELDS,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for key in sorted(cells):
            cell = cells[key]
            unique_issues = list(dict.fromkeys(cell.issues))
            row = dict(zip(GROUP_FIELDS, key))
            row["correctness_status"] = (
                "fail" if unique_issues else "pass"
            )
            for field in (
                EXTERNAL_DIRECTIONAL_ORACLE_GATE_PASS,
                *EXTERNAL_DUMI_ORACLE_REQUIRED_FIELDS,
                *EXTERNAL_DIRECTIONAL_DIAGNOSTIC_FIELDS,
                EXTERNAL_DIRECTIONAL_ORACLE_RECEIPT,
            ):
                values = sorted(
                    {
                        source.get(field, "")
                        for source in cell.rows
                        if source.get(field, "")
                    }
                )
                row[field] = ",".join(values)
            row["issue_count"] = str(len(unique_issues))
            row["issues"] = " | ".join(unique_issues)
            writer.writerow(row)
    finally:
        if should_close:
            stream.close()


def write_comparisons(
    rows: list[dict[str, str]],
    output: str | None,
) -> None:
    if output is None:
        return
    stream, should_close = output_stream(output)
    try:
        writer = csv.DictWriter(
            stream,
            fieldnames=COMPARISON_FIELDS,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
    finally:
        if should_close:
            stream.close()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate benchmark run TSV data and report per-cell median, min, "
            "max, range, and median absolute deviation (MAD)."
        )
    )
    parser.add_argument("results_tsv", help="raw benchmark run TSV")
    parser.add_argument(
        "--output",
        help="write summary TSV to this path instead of standard output",
    )
    parser.add_argument(
        "--correctness-output",
        help="write a per-cell correctness TSV to this path",
    )
    parser.add_argument(
        "--comparisons-output",
        help=(
            "write matched-repetition comparisons against canonical-upstream "
            "legacy to this path"
        ),
    )
    parser.add_argument(
        "--design-tsv",
        help=(
            "planned measured-stage rows; require an exact match on run_id, "
            "workload, scale, stage, implementation, mode, repetition, and order"
        ),
    )
    parser.add_argument(
        "--expected-repetitions",
        type=int,
        help="require exactly this many attempts in every summarized cell",
    )
    return parser.parse_args()


def validate_path_separation(
    input_paths: Iterable[Path],
    output_paths: Iterable[str | None],
) -> None:
    protected = {path.resolve() for path in input_paths}
    destinations = [
        Path(path).resolve()
        for path in output_paths
        if path is not None and path != "-"
    ]
    if len(destinations) != len(set(destinations)):
        raise SummaryError("summary and correctness outputs must be different files")
    overlap = protected.intersection(destinations)
    if overlap:
        raise SummaryError(
            "output path must not overwrite an input: "
            + ", ".join(str(path) for path in sorted(overlap))
        )


def main() -> int:
    arguments = parse_arguments()
    try:
        if (
            arguments.expected_repetitions is not None
            and arguments.expected_repetitions < 1
        ):
            raise SummaryError("--expected-repetitions must be positive")
        path = Path(arguments.results_tsv)
        if not path.is_file():
            raise SummaryError(f"input is not a regular file: {path}")
        input_paths = [path]
        if arguments.design_tsv:
            input_paths.append(Path(arguments.design_tsv))
        validate_path_separation(
            input_paths,
            (
                arguments.output,
                arguments.correctness_output,
                arguments.comparisons_output,
            ),
        )
        stdout_outputs = sum(
            output == "-"
            or (label == "summary" and output is None)
            for label, output in (
                ("summary", arguments.output),
                ("correctness", arguments.correctness_output),
                ("comparisons", arguments.comparisons_output),
            )
        )
        if stdout_outputs > 1:
            raise SummaryError(
                "only one output may use standard output"
            )
        rows = read_rows(path)
        structural_issues = validate_rows(rows)
        design_issues: list[str] = []
        if arguments.design_tsv:
            design_path = Path(arguments.design_tsv)
            if not design_path.is_file():
                raise SummaryError(
                    f"design is not a regular file: {design_path}"
                )
            design_issues = apply_design(rows, read_design(design_path))
        cells = group_cells(rows)
        correctness_issues = check_correctness(
            rows,
            cells,
            arguments.expected_repetitions,
        )
        summaries = [summarize_cell(cells[key]) for key in sorted(cells)]
        comparisons, comparison_issues = build_comparisons(rows, cells)
        write_summary(summaries, arguments.output)
        write_correctness(cells, arguments.correctness_output)
        write_comparisons(comparisons, arguments.comparisons_output)
        issues = (
            structural_issues
            + design_issues
            + correctness_issues
            + comparison_issues
        )
        if issues:
            for issue in dict.fromkeys(issues):
                print(f"error: {issue}", file=sys.stderr)
            return 1
        return 0
    except SummaryError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
