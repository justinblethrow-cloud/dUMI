#!/usr/bin/env python3
"""Regression contracts for external-BAM benchmark summarization."""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SUMMARIZER_PATH = ROOT / "scripts" / "benchmark" / "summarize_results.py"
SPEC = importlib.util.spec_from_file_location(
    "dumi_benchmark_summarizer", SUMMARIZER_PATH
)
assert SPEC is not None and SPEC.loader is not None
SUMMARIZER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SUMMARIZER
SPEC.loader.exec_module(SUMMARIZER)

EXTRA_FIELDS = (
    "actual_route",
    "oracle_implementation",
    "exact_oracle_match",
    "cross_implementation_exact_match",
    "cross_implementation_output_count_match",
    "cross_implementation_alignment_group_output_count_match",
    "directional_oracle_gate_pass",
    "dumi_off_oracle_partition_equivalent",
    "dumi_off_oracle_root_assignment_equivalent",
    "canonical_upstream_oracle_partition_equivalent",
    "canonical_upstream_oracle_root_assignment_equivalent",
    "canonical_upstream_dumi_off_partition_equivalent",
    "canonical_upstream_dumi_off_root_assignment_equivalent",
    "directional_oracle_receipt",
)


def benchmark_rows(workload: str = "external") -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    treatments = (
        {
            "implementation": "canonical-upstream",
            "mode": "legacy",
            "order": "1",
            "output_records": "10",
            "semantic_sha256": "b" * 64,
            "actual_route": "coordinate",
            "oracle_implementation": "canonical-upstream",
            "elapsed_s": "2",
            "max_rss_kib": "100",
        },
        {
            "implementation": "dumi",
            "mode": "off",
            "order": "2",
            "output_records": "10",
            "semantic_sha256": "c" * 64,
            "actual_route": "off",
            "oracle_implementation": "dumi-off",
            "elapsed_s": "1",
            "max_rss_kib": "50",
        },
        {
            "implementation": "dumi",
            "mode": "auto",
            "order": "3",
            "output_records": "10",
            "semantic_sha256": "c" * 64,
            "actual_route": "streaming",
            "oracle_implementation": "dumi-off",
            "elapsed_s": "0.8",
            "max_rss_kib": "45",
        },
    )
    end_to_end_orders = {
        ("canonical-upstream", "legacy"): "2",
        ("dumi", "off"): "3",
        ("dumi", "auto"): "1",
    }
    for treatment in treatments:
        for stage in SUMMARIZER.MEASURED_STAGES:
            row = {field: "" for field in SUMMARIZER.RAW_FIELDS}
            row.update(
                {
                    "run_id": (
                        f"{treatment['implementation']}-{treatment['mode']}-{stage}"
                    ),
                    "workload": workload,
                    "scale": "demo-se-01",
                    "stage": stage,
                    "implementation": treatment["implementation"],
                    "mode": treatment["mode"],
                    "repetition": "1",
                    "order": (
                        treatment["order"]
                        if stage == "raw"
                        else end_to_end_orders[
                            (
                                treatment["implementation"],
                                treatment["mode"],
                            )
                        ]
                    ),
                    "exit_code": "0",
                    "elapsed_s": treatment["elapsed_s"],
                    "user_s": "0.5",
                    "system_s": "0.1",
                    "cpu_pct": "60",
                    "max_rss_kib": treatment["max_rss_kib"],
                    "input_sha256": "a" * 64,
                    "output_records": treatment["output_records"],
                    "semantic_sha256": treatment["semantic_sha256"],
                    "sort_order": (
                        "coordinate"
                        if stage == "end_to_end_ready"
                        or treatment["actual_route"] != "streaming"
                        else "unsorted"
                    ),
                    "output_bytes": "100",
                    "output_sha256": "d" * 64,
                    "reference_sequences": "1",
                    "reference_dictionary_sha256": "e" * 64,
                    "expected_output_records": treatment["output_records"],
                    "expected_semantic_sha256": treatment["semantic_sha256"],
                    "expected_reference_sequences": "1",
                    "expected_reference_dictionary_sha256": "e" * 64,
                    "actual_route": treatment["actual_route"],
                    "oracle_implementation": treatment["oracle_implementation"],
                    "exact_oracle_match": "True",
                    "cross_implementation_exact_match": "False",
                    "cross_implementation_output_count_match": "True",
                    (
                        "cross_implementation_alignment_group_output_count_match"
                    ): "True",
                    "directional_oracle_gate_pass": (
                        "True" if workload == "external" else ""
                    ),
                    "dumi_off_oracle_partition_equivalent": (
                        "True" if workload == "external" else ""
                    ),
                    "dumi_off_oracle_root_assignment_equivalent": (
                        "True" if workload == "external" else ""
                    ),
                    "canonical_upstream_oracle_partition_equivalent": (
                        "False" if workload == "external" else ""
                    ),
                    "canonical_upstream_oracle_root_assignment_equivalent": (
                        "False" if workload == "external" else ""
                    ),
                    "canonical_upstream_dumi_off_partition_equivalent": (
                        "False" if workload == "external" else ""
                    ),
                    "canonical_upstream_dumi_off_root_assignment_equivalent": (
                        "False" if workload == "external" else ""
                    ),
                    "directional_oracle_receipt": (
                        "validation/external/demo-se-01/"
                        "directional-oracle-receipt.json"
                        if workload == "external"
                        else ""
                    ),
                }
            )
            rows.append(row)
    return rows


class ExternalSummaryContracts(unittest.TestCase):
    def run_summary(
        self,
        rows: list[dict[str, str]],
        *,
        include_gate_column: bool = True,
        expected_repetitions: int = 1,
    ) -> tuple[
        subprocess.CompletedProcess[str],
        list[dict[str, str]],
        list[dict[str, str]],
    ]:
        with tempfile.TemporaryDirectory() as directory_string:
            directory = Path(directory_string)
            measurements = directory / "measurements.tsv"
            summary = directory / "summary.tsv"
            correctness = directory / "correctness.tsv"
            comparisons = directory / "comparisons.tsv"
            fieldnames = list(SUMMARIZER.RAW_FIELDS) + list(EXTRA_FIELDS)
            if not include_gate_column:
                fieldnames.remove(
                    "cross_implementation_alignment_group_output_count_match"
                )
            with measurements.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=fieldnames,
                    delimiter="\t",
                    lineterminator="\n",
                    extrasaction="ignore",
                )
                writer.writeheader()
                writer.writerows(rows)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SUMMARIZER_PATH),
                    str(measurements),
                    "--output",
                    str(summary),
                    "--correctness-output",
                    str(correctness),
                    "--comparisons-output",
                    str(comparisons),
                    "--expected-repetitions",
                    str(expected_repetitions),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            comparison_rows: list[dict[str, str]] = []
            self.summary_rows = []
            if summary.is_file():
                with summary.open(
                    "r", encoding="utf-8", newline=""
                ) as stream:
                    self.summary_rows = list(
                        csv.DictReader(stream, delimiter="\t")
                    )
            if comparisons.is_file():
                with comparisons.open(
                    "r", encoding="utf-8", newline=""
                ) as stream:
                    comparison_rows = list(
                        csv.DictReader(stream, delimiter="\t")
                    )
            correctness_rows: list[dict[str, str]] = []
            if correctness.is_file():
                with correctness.open(
                    "r", encoding="utf-8", newline=""
                ) as stream:
                    correctness_rows = list(
                        csv.DictReader(stream, delimiter="\t")
                    )
            return completed, comparison_rows, correctness_rows

    def test_external_implementation_specific_exact_oracles_pass(self) -> None:
        completed, comparisons, correctness = self.run_summary(
            benchmark_rows()
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            {row["stage"] for row in comparisons},
            {"raw", "end_to_end_ready"},
        )
        self.assertEqual(len(comparisons), 4)
        self.assertTrue(
            all(row["correctness_status"] == "pass" for row in comparisons)
        )
        self.assertTrue(
            all(
                row["successful_pairs"] == "1"
                and row["noncomparable_pairs"] == "0"
                and row["comparability_status"] == "comparable"
                and row["comparability_issues"] == ""
                for row in comparisons
            )
        )
        self.assertTrue(
            all(
                row["comparability_status"] == "comparable"
                and row["comparability_issues"] == ""
                for row in self.summary_rows
            )
        )
        self.assertTrue(correctness)
        self.assertTrue(
            all(
                row["directional_oracle_gate_pass"] == "True"
                and row["dumi_off_oracle_partition_equivalent"] == "True"
                and row["dumi_off_oracle_root_assignment_equivalent"] == "True"
                and row["canonical_upstream_oracle_partition_equivalent"]
                == "False"
                and row["directional_oracle_receipt"]
                == (
                    "validation/external/demo-se-01/"
                    "directional-oracle-receipt.json"
                )
                for row in correctness
            )
        )

    def test_only_directly_measured_stages_are_summarized(self) -> None:
        completed, comparisons, correctness = self.run_summary(
            benchmark_rows()
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            {row["stage"] for row in comparisons},
            set(SUMMARIZER.MEASURED_STAGES),
        )
        self.assertEqual(
            {row["stage"] for row in correctness},
            set(SUMMARIZER.MEASURED_STAGES),
        )

    def test_design_accepts_independent_stage_orders(self) -> None:
        design = [
            {field: row[field] for field in SUMMARIZER.DESIGN_FIELDS}
            for row in benchmark_rows()
        ]
        SUMMARIZER.validate_design_schedule(design)
        raw_orders = {
            (row["implementation"], row["mode"]): row["order"]
            for row in design
            if row["stage"] == "raw"
        }
        end_to_end_orders = {
            (row["implementation"], row["mode"]): row["order"]
            for row in design
            if row["stage"] == "end_to_end_ready"
        }
        self.assertNotEqual(raw_orders, end_to_end_orders)

    def test_design_requires_each_directly_measured_stage(self) -> None:
        design = [
            {field: row[field] for field in SUMMARIZER.DESIGN_FIELDS}
            for row in benchmark_rows()
            if not (
                row["implementation"] == "dumi"
                and row["mode"] == "off"
                and row["stage"] == "end_to_end_ready"
            )
        ]
        with self.assertRaisesRegex(
            SUMMARIZER.SummaryError, "every measured stage"
        ):
            SUMMARIZER.validate_design_schedule(design)

    def test_synthetic_cross_implementation_divergence_remains_invalid(
        self,
    ) -> None:
        completed, _, _ = self.run_summary(benchmark_rows("moderate"))
        self.assertEqual(completed.returncode, 1)
        self.assertIn(
            "non-equivalent record multisets across cells", completed.stderr
        )

    def test_external_row_must_still_match_its_own_exact_oracle(self) -> None:
        rows = benchmark_rows()
        rows[-1]["semantic_sha256"] = "f" * 64
        completed, _, _ = self.run_summary(rows)
        self.assertEqual(completed.returncode, 1)
        self.assertIn(
            "semantic_sha256 does not match recorded workload oracle",
            completed.stderr,
        )

    def test_all_dumi_modes_must_share_the_dumi_off_exact_oracle(self) -> None:
        rows = benchmark_rows()
        for row in rows:
            if row["implementation"] == "dumi" and row["mode"] == "auto":
                row["semantic_sha256"] = "f" * 64
                row["expected_semantic_sha256"] = "f" * 64
        completed, comparisons, _ = self.run_summary(rows)
        self.assertEqual(completed.returncode, 1)
        self.assertIn(
            "inconsistent exact evidence for external oracle dumi-off",
            completed.stderr,
        )
        auto_comparisons = [
            row for row in comparisons if row["mode"] == "auto"
        ]
        self.assertTrue(auto_comparisons)
        self.assertTrue(
            all(
                row["correctness_status"] == "fail"
                and row["successful_pairs"] == "0"
                for row in auto_comparisons
            )
        )

    def test_dumi_repetitions_must_share_the_dumi_off_exact_oracle(
        self,
    ) -> None:
        rows = benchmark_rows()
        second_repetition: list[dict[str, str]] = []
        for row in rows:
            repeated = dict(row)
            repeated["run_id"] = "r2-" + repeated["run_id"]
            repeated["repetition"] = "2"
            if repeated["implementation"] == "dumi":
                repeated["semantic_sha256"] = "f" * 64
                repeated["expected_semantic_sha256"] = "f" * 64
            second_repetition.append(repeated)
        completed, comparisons, _ = self.run_summary(
            rows + second_repetition, expected_repetitions=2
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn(
            "inconsistent expected evidence for external oracle dumi-off",
            completed.stderr,
        )
        self.assertTrue(comparisons)
        self.assertTrue(
            all(row["correctness_status"] == "fail" for row in comparisons)
        )

    def test_output_count_mismatch_is_valid_but_not_comparable(
        self,
    ) -> None:
        rows = benchmark_rows()
        for row in rows:
            if row["implementation"] == "dumi":
                row["output_records"] = "9"
                row["expected_output_records"] = "9"
            row["cross_implementation_output_count_match"] = "False"
        completed, comparisons, _ = self.run_summary(rows)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(
            all(
                row["correctness_status"] == "pass"
                and row["comparability_status"] == "not_comparable"
                and row["comparability_issues"]
                == "cross-implementation-output-count-mismatch"
                and row["successful_pairs"] == "0"
                and row["failed_pairs"] == "0"
                and row["noncomparable_pairs"] == "1"
                and row["elapsed_speedup_n"] == "0"
                and row["elapsed_speedup_median"] == ""
                for row in comparisons
            )
        )
        self.assertTrue(
            all(
                row["comparability_status"] == "not_comparable"
                and row["comparability_issues"]
                == "cross-implementation-output-count-mismatch"
                for row in self.summary_rows
            )
        )

    def test_external_oracle_metadata_fails_closed(self) -> None:
        scenarios = (
            (
                "exact-false",
                "exact_oracle_match",
                "False",
                "external measurement is missing a true exact_oracle_match",
            ),
            (
                "oracle-mislabeled",
                "oracle_implementation",
                "dumi-off",
                "external oracle_implementation does not match",
            ),
            (
                "cross-exact-invalid",
                "cross_implementation_exact_match",
                "not-a-boolean",
                "external measurement is missing a valid boolean "
                "cross_implementation_exact_match",
            ),
        )
        for name, field, value, expected_error in scenarios:
            with self.subTest(name=name):
                rows = benchmark_rows()
                rows[0][field] = value
                completed, _, _ = self.run_summary(rows)
                self.assertEqual(completed.returncode, 1)
                self.assertIn(expected_error, completed.stderr)

    def test_directional_oracle_metadata_is_required_and_consistent(
        self,
    ) -> None:
        scenarios = (
            (
                "gate-missing",
                "directional_oracle_gate_pass",
                "",
                "external measurement is missing a true "
                "directional_oracle_gate_pass",
            ),
            (
                "gate-false",
                "directional_oracle_gate_pass",
                "False",
                "external measurement is missing a true "
                "directional_oracle_gate_pass",
            ),
            (
                "dumi-root-false",
                "dumi_off_oracle_root_assignment_equivalent",
                "False",
                "external measurement is missing a true "
                "dumi_off_oracle_root_assignment_equivalent",
            ),
            (
                "diagnostic-invalid",
                "canonical_upstream_oracle_partition_equivalent",
                "not-a-boolean",
                "external measurement is missing a valid boolean "
                "canonical_upstream_oracle_partition_equivalent",
            ),
            (
                "receipt-missing",
                "directional_oracle_receipt",
                "",
                "external measurement is missing a nonempty "
                "directional_oracle_receipt",
            ),
            (
                "receipt-inconsistent",
                "directional_oracle_receipt",
                "validation/external/demo-se-01/other-receipt.json",
                "inconsistent directional_oracle_receipt metadata",
            ),
        )
        for name, field, value, expected_error in scenarios:
            with self.subTest(name=name):
                rows = benchmark_rows()
                if name == "receipt-inconsistent":
                    rows[0][field] = value
                else:
                    for row in rows:
                        row[field] = value
                completed, comparisons, correctness = self.run_summary(rows)
                self.assertEqual(completed.returncode, 1)
                self.assertIn(expected_error, completed.stderr)
                self.assertTrue(comparisons)
                self.assertTrue(
                    all(
                        row["correctness_status"] == "fail"
                        and row["successful_pairs"] == "0"
                        for row in comparisons
                    )
                )
                self.assertTrue(
                    all(
                        "directional_oracle_gate_pass" in row
                        and "directional_oracle_receipt" in row
                        for row in correctness
                    )
                )

    def test_cross_implementation_exact_metadata_is_required_and_consistent(
        self,
    ) -> None:
        scenarios = ("missing", "inconsistent")
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                rows = benchmark_rows()
                if scenario == "missing":
                    for row in rows:
                        row["cross_implementation_exact_match"] = ""
                    expected_error = (
                        "external measurement is missing a valid boolean "
                        "cross_implementation_exact_match"
                    )
                else:
                    rows[0]["cross_implementation_exact_match"] = "True"
                    expected_error = (
                        "inconsistent cross_implementation_exact_match metadata"
                    )
                completed, comparisons, _ = self.run_summary(rows)
                self.assertEqual(completed.returncode, 1)
                self.assertIn(expected_error, completed.stderr)
                self.assertTrue(
                    all(
                        row["correctness_status"] == "fail"
                        for row in comparisons
                    )
                )

    def test_cross_implementation_exact_metadata_must_match_evidence(
        self,
    ) -> None:
        scenarios = ("false-positive", "false-negative")
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                rows = benchmark_rows()
                if scenario == "false-positive":
                    for row in rows:
                        row["cross_implementation_exact_match"] = "True"
                else:
                    for row in rows:
                        if row["implementation"] == "dumi":
                            row["semantic_sha256"] = "b" * 64
                            row["expected_semantic_sha256"] = "b" * 64
                completed, comparisons, _ = self.run_summary(rows)
                self.assertEqual(completed.returncode, 1)
                self.assertIn(
                    "cross_implementation_exact_match inconsistent with "
                    "the implementation-oracle evidence",
                    completed.stderr,
                )
                self.assertTrue(
                    all(
                        row["correctness_status"] == "fail"
                        and row["successful_pairs"] == "0"
                        for row in comparisons
                    )
                )

    def test_cross_output_count_metadata_is_required_and_evidence_bound(
        self,
    ) -> None:
        scenarios = ("missing", "inconsistent", "false-positive", "false-negative")
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                rows = benchmark_rows()
                if scenario == "missing":
                    for row in rows:
                        row["cross_implementation_output_count_match"] = ""
                    expected_error = (
                        "external measurement is missing a valid boolean "
                        "cross_implementation_output_count_match"
                    )
                elif scenario == "inconsistent":
                    rows[0][
                        "cross_implementation_output_count_match"
                    ] = "False"
                    expected_error = (
                        "inconsistent "
                        "cross_implementation_output_count_match metadata"
                    )
                elif scenario == "false-positive":
                    for row in rows:
                        if row["implementation"] == "dumi":
                            row["output_records"] = "9"
                            row["expected_output_records"] = "9"
                    expected_error = (
                        "cross_implementation_output_count_match "
                        "inconsistent with the implementation-oracle evidence"
                    )
                else:
                    for row in rows:
                        row[
                            "cross_implementation_output_count_match"
                        ] = "False"
                    expected_error = (
                        "cross_implementation_output_count_match "
                        "inconsistent with the implementation-oracle evidence"
                    )
                completed, _, _ = self.run_summary(rows)
                self.assertEqual(completed.returncode, 1)
                self.assertIn(expected_error, completed.stderr)

    def test_external_input_evidence_must_match(self) -> None:
        scenarios = (
            (
                "input",
                "input_sha256",
                "f" * 64,
                "uses different input hashes across cells",
            ),
        )
        for name, field, value, expected_error in scenarios:
            with self.subTest(name=name):
                rows = benchmark_rows()
                for row in rows:
                    if row["implementation"] == "dumi":
                        row[field] = value
                completed, comparisons, _ = self.run_summary(rows)
                self.assertEqual(completed.returncode, 1)
                self.assertIn(expected_error, completed.stderr)
                self.assertTrue(
                    all(
                        row["correctness_status"] == "fail"
                        and row["successful_pairs"] == "0"
                        for row in comparisons
                    )
                )

    def test_end_to_end_divergence_fails_direct_comparisons(self) -> None:
        rows = benchmark_rows()
        for row in rows:
            if (
                row["implementation"] == "canonical-upstream"
                and row["stage"] == "end_to_end_ready"
            ):
                row["semantic_sha256"] = "f" * 64
                row["expected_semantic_sha256"] = "f" * 64
        completed, comparisons, _ = self.run_summary(rows)
        self.assertEqual(completed.returncode, 1)
        end_to_end = [
            row
            for row in comparisons
            if row["stage"] == "end_to_end_ready"
        ]
        self.assertTrue(end_to_end)
        self.assertTrue(
            all(
                row["correctness_status"] == "fail"
                and row["successful_pairs"] == "0"
                for row in end_to_end
            )
        )

    def test_cross_implementation_alignment_group_diagnostic_is_boolean(
        self,
    ) -> None:
        scenarios = ("missing", "false", "inconsistent")
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                rows = benchmark_rows()
                include_gate_column = scenario != "missing"
                if scenario == "false":
                    for row in rows:
                        row[
                            "cross_implementation_alignment_group_output_count_match"
                        ] = "False"
                elif scenario == "inconsistent":
                    for row in rows:
                        if row["implementation"] == "dumi":
                            row[
                                "cross_implementation_alignment_group_output_count_match"
                            ] = "False"
                completed, _, _ = self.run_summary(
                    rows, include_gate_column=include_gate_column
                )
                if scenario == "false":
                    self.assertEqual(
                        completed.returncode, 0, completed.stderr
                    )
                else:
                    self.assertEqual(completed.returncode, 1)
                    self.assertIn(
                        (
                            "external measurement is missing a valid boolean "
                            "cross_implementation_alignment_group_output_count_match"
                            if scenario == "missing"
                            else "inconsistent "
                            "cross_implementation_alignment_group_output_count_match"
                        ),
                        completed.stderr,
                    )


if __name__ == "__main__":
    unittest.main()
