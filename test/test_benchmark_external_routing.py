#!/usr/bin/env python3
"""Focused contracts for external-input routing and private snapshots."""

from __future__ import annotations

import csv
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "scripts" / "benchmark" / "run_benchmark.py"
SPEC = importlib.util.spec_from_file_location("dumi_benchmark_runner", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUNNER
SPEC.loader.exec_module(RUNNER)


def external_workload(*, eligible: bool) -> object:
    entry = RUNNER.ExternalBamInput(
        workload_id="demo-se-01",
        bam_path=Path("/not-opened/input.bam"),
        bam_sha256="0" * 64,
        paired=False,
        umi_length=12,
        umi_separator="_",
        rationale="",
    )
    return RUNNER.Workload(
        "external",
        entry.workload_id,
        entry.umi_length,
        entry.paired,
        (),
        external_input=entry,
        streaming_on_eligible=eligible,
    )


class ExternalRoutingContracts(unittest.TestCase):
    @staticmethod
    def oracle_receipt(
        *,
        exact: bool,
        groups_equal: bool = True,
        output_records: int = 10,
    ) -> dict[str, object]:
        return {
            "output_records": output_records,
            "alignment_group_output_records": 9,
            "alignment_group_records_excluded_unmapped": 1,
            "alignment_group_records_excluded_second_of_pair": 0,
            "record_equivalent": exact,
            "reference_dictionary_equivalent": True,
            "read_group_dictionary_equivalent": True,
            "alignment_group_output_count_equivalent": groups_equal,
        }

    def test_ineligible_input_keeps_upstream_off_and_auto_cells(self) -> None:
        labels = [
            implementation.label
            for implementation in RUNNER.implementations_for(
                external_workload(eligible=False), False
            )
        ]
        self.assertEqual(
            labels,
            ["canonical-upstream", "dumi-off", "dumi-auto"],
        )

    def test_eligible_input_adds_forced_on_cell(self) -> None:
        labels = [
            implementation.label
            for implementation in RUNNER.implementations_for(
                external_workload(eligible=True), False
            )
        ]
        self.assertEqual(
            labels,
            ["canonical-upstream", "dumi-off", "dumi-on", "dumi-auto"],
        )

    def test_four_treatment_eight_repetition_williams_schedule(self) -> None:
        implementations = RUNNER.implementations_for(
            external_workload(eligible=True), False
        )
        schedule = RUNNER.workload_stage_schedule(
            implementations, 8, 0
        )
        by_stage = {
            stage: [cell for candidate, cell in schedule if candidate == stage]
            for stage in RUNNER.MEASURED_STAGES
        }
        for stage, cells in by_stage.items():
            position_counts = Counter(
                (cell.implementation.label, cell.order)
                for cell in cells
            )
            self.assertEqual(set(position_counts.values()), {2}, stage)
            carryover = Counter()
            for repetition in range(1, 9):
                ordered = [
                    cell.implementation.label
                    for cell in cells
                    if cell.repetition == repetition
                ]
                carryover.update(zip(ordered, ordered[1:]))
            self.assertEqual(len(carryover), 12, stage)
            self.assertEqual(set(carryover.values()), {2}, stage)
        raw_orders = {
            cell.repetition: [
                candidate.implementation.label
                for candidate in by_stage["raw"]
                if candidate.repetition == cell.repetition
            ]
            for cell in by_stage["raw"]
        }
        end_to_end_orders = {
            cell.repetition: [
                candidate.implementation.label
                for candidate in by_stage["end_to_end_ready"]
                if candidate.repetition == cell.repetition
            ]
            for cell in by_stage["end_to_end_ready"]
        }
        self.assertTrue(
            all(
                raw_orders[repetition]
                != end_to_end_orders[repetition]
                for repetition in raw_orders
            )
        )

    def test_gnu_sort_resolution_prefers_gsort_and_rejects_bsd(self) -> None:
        gnu_version = subprocess.CompletedProcess(
            ["/opt/homebrew/bin/gsort", "--version"],
            0,
            "sort (GNU coreutils) 9.5\n",
            "",
        )
        with (
            mock.patch.object(
                RUNNER.shutil,
                "which",
                side_effect=lambda candidate: (
                    "/opt/homebrew/bin/gsort"
                    if candidate == "gsort"
                    else "/usr/bin/sort"
                ),
            ),
            mock.patch.object(
                RUNNER,
                "run_command",
                return_value=gnu_version,
            ) as run,
        ):
            self.assertEqual(
                RUNNER.find_gnu_sort(None),
                Path("/opt/homebrew/bin/gsort"),
            )
            run.assert_called_once()

        bsd_version = subprocess.CompletedProcess(
            ["/usr/bin/sort", "--version"],
            1,
            "",
            "sort: illegal option -- -\n",
        )
        with (
            mock.patch.object(
                RUNNER.shutil, "which", return_value="/usr/bin/sort"
            ),
            mock.patch.object(
                RUNNER,
                "run_command",
                return_value=bsd_version,
            ),
            self.assertRaisesRegex(
                RUNNER.BenchmarkError,
                "GNU sort is required",
            ),
        ):
            RUNNER.find_gnu_sort("sort")

    def test_complete_block_is_timed_before_validation(self) -> None:
        implementations = RUNNER.implementations_for(
            external_workload(eligible=True), False
        )
        schedule = RUNNER.workload_stage_schedule(
            implementations, 2, 0
        )
        events: list[tuple[str, str, int]] = []

        def time_cell(stage: str, cell: object) -> object:
            events.append(
                ("time", stage, cell.repetition)
            )
            return stage, cell

        def validate_cell(pending: object) -> dict[str, object]:
            stage, cell = pending
            events.append(
                ("validate", stage, cell.repetition)
            )
            return {"stage": stage}

        rows = RUNNER.execute_timing_blocks(
            scheduled_cells=schedule,
            repetitions=2,
            treatments_per_block=4,
            time_cell=time_cell,
            validate_cell=validate_cell,
        )
        self.assertEqual(len(rows), 16)
        for offset in range(0, len(events), 8):
            block = events[offset : offset + 8]
            self.assertEqual(
                [event[0] for event in block],
                ["time"] * 4 + ["validate"] * 4,
            )
            self.assertEqual(
                {event[1:] for event in block[:4]},
                {block[0][1:]},
            )

    def test_end_to_end_ready_command_starts_with_fresh_java(self) -> None:
        unsorted = RUNNER.build_end_to_end_ready_command(
            java_command=["java", "-jar", "dedup.jar", "-o", "raw.bam"],
            raw_sort_order="unsorted",
            java_output=Path("raw.bam"),
            final_output=Path("final.bam"),
            samtools=Path("/usr/bin/samtools"),
        )
        self.assertEqual(unsorted[:2], ["bash", "-c"])
        shell = unsorted[2]
        self.assertLess(shell.index("java"), shell.index("samtools sort"))
        self.assertLess(shell.index("samtools sort"), shell.index("samtools index"))

        coordinate = RUNNER.build_end_to_end_ready_command(
            java_command=["java", "-jar", "dedup.jar", "-o", "final.bam"],
            raw_sort_order="coordinate",
            java_output=Path("final.bam"),
            final_output=Path("final.bam"),
            samtools=Path("/usr/bin/samtools"),
        )
        self.assertNotIn(" sort ", coordinate[2])
        self.assertIn(" index ", coordinate[2])

    def test_java_directional_parameters_are_explicit(self) -> None:
        command = [
            str(value)
            for value in RUNNER.build_java_bam_command(
                java=Path("/fake/java"),
                jvm_options=(),
                java_tmp=Path("/private/tmp"),
                classes_root=Path("/classes"),
                common_classpath="/dependencies",
                bam_input=Path("/private/input.bam"),
                output=Path("/private/output.bam"),
                workload=external_workload(eligible=True),
                source_key="dumi",
                streaming_mode="off",
            )
        ]
        self.assertEqual(command[command.index("-k") + 1], "1")
        self.assertEqual(command[command.index("-p") + 1], ".5")

    def test_stage_scratch_gate_accounts_for_complete_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory_string:
            directory = Path(directory_string)
            bam = directory / "input.bam"
            bam.write_bytes(b"x" * 100)
            receipt_path = directory / "capacity.json"
            with mock.patch.object(
                RUNNER.shutil,
                "disk_usage",
                return_value=mock.Mock(free=1024**3),
            ):
                receipt = RUNNER.require_stage_scratch_capacity(
                    output_root=directory,
                    bam_input=bam,
                    treatments_per_block=4,
                    directional_oracle_record_count=10,
                    directional_oracle_paired=False,
                    directional_oracle_umi_length=12,
                    receipt_path=receipt_path,
                )
            self.assertEqual(
                receipt["retained_block_output_allowances"], 8
            )
            self.assertEqual(
                receipt["samtools_sort_scratch_allowances"], 1
            )
            self.assertEqual(
                receipt["directional_oracle_source_record_key_bytes"],
                10 * (17 + 12 + 2),
            )
            self.assertEqual(
                receipt["directional_oracle_tagged_record_key_bytes_each"],
                10 * (17 + 2 * 12 + 12),
            )
            self.assertEqual(
                receipt[
                    "directional_oracle_membership_canonical_bytes_each"
                ],
                10 * (17 + 12 + 13),
            )
            self.assertEqual(
                receipt[
                    "directional_oracle_rooted_canonical_bytes_each"
                ],
                10 * (17 + 2 * 12 + 14),
            )
            self.assertEqual(
                receipt[
                    "directional_oracle_alignment_umi_aggregate_bytes_each"
                ],
                10 * (17 + 12 + 13),
            )
            self.assertEqual(
                receipt[
                    "directional_oracle_concurrent_sort_buffer_memory_bytes"
                ],
                3 * 256 * 1024 * 1024,
            )
            tagged_key = 10 * (17 + 2 * 12 + 12)
            membership = 10 * (17 + 12 + 13)
            rooted = 10 * (17 + 2 * 12 + 14)
            tagged_bam_each = max(
                125 + 10 * (12 + 32),
                100 + 10 * (2 * 12 + 64),
            )
            self.assertEqual(
                receipt["directional_oracle_peak_stage_bytes"],
                2 * tagged_bam_each
                + tagged_key
                + 6 * membership
                + 4 * rooted,
            )
            self.assertGreater(
                receipt["directional_oracle_peak_stage_bytes"],
                receipt["timing_peak_stage_bytes"],
            )
            self.assertEqual(
                json.loads(receipt_path.read_text(encoding="utf-8")),
                receipt,
            )
            insufficient_receipt = directory / "insufficient-capacity.json"
            required = int(receipt["required_available_bytes"])
            with (
                mock.patch.object(
                    RUNNER.shutil,
                    "disk_usage",
                    return_value=mock.Mock(free=required - 1),
                ),
                self.assertRaisesRegex(
                    RUNNER.BenchmarkError,
                    "insufficient scratch capacity",
                ),
            ):
                RUNNER.require_stage_scratch_capacity(
                    output_root=directory,
                    bam_input=bam,
                    treatments_per_block=4,
                    directional_oracle_record_count=10,
                    directional_oracle_paired=False,
                    directional_oracle_umi_length=12,
                    receipt_path=insufficient_receipt,
                )
            failed_receipt = json.loads(
                insufficient_receipt.read_text(encoding="utf-8")
            )
            self.assertEqual(failed_receipt["status"], "insufficient")
            self.assertEqual(
                failed_receipt["available_bytes"], required - 1
            )
            self.assertEqual(
                failed_receipt["required_available_bytes"], required
            )
            with mock.patch.object(
                RUNNER.shutil,
                "disk_usage",
                return_value=mock.Mock(free=1024**4),
            ):
                oracle_only = RUNNER.require_stage_scratch_capacity(
                    output_root=directory,
                    bam_input=bam,
                    treatments_per_block=100000,
                    directional_oracle_record_count=10,
                    directional_oracle_umi_length=12,
                    directional_oracle_only=True,
                )
            self.assertEqual(
                oracle_only["scope"],
                "deferred-directional-oracle-only",
            )
            self.assertGreater(
                oracle_only["timing_peak_stage_bytes"],
                oracle_only["directional_oracle_peak_stage_bytes"],
            )
            self.assertEqual(
                oracle_only["peak_stage_output_bytes"],
                oracle_only["directional_oracle_peak_stage_bytes"],
            )

    def test_stage_scratch_gate_rejects_invalid_oracle_sizing_inputs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory_string:
            directory = Path(directory_string)
            bam = directory / "input.bam"
            bam.write_bytes(b"x")
            with self.assertRaisesRegex(
                RUNNER.BenchmarkError,
                "record count must be positive",
            ):
                RUNNER.require_stage_scratch_capacity(
                    output_root=directory,
                    bam_input=bam,
                    treatments_per_block=1,
                    directional_oracle_record_count=0,
                    directional_oracle_umi_length=12,
                )
            with self.assertRaisesRegex(
                RUNNER.BenchmarkError,
                "UMI length must be positive",
            ):
                RUNNER.require_stage_scratch_capacity(
                    output_root=directory,
                    bam_input=bam,
                    treatments_per_block=1,
                    directional_oracle_record_count=1,
                )

    def test_directional_receipt_is_cross_bound_to_metrics_and_inputs(
        self,
    ) -> None:
        workload = external_workload(eligible=True)
        assert workload.external_input is not None
        with tempfile.TemporaryDirectory() as directory_string:
            directory = Path(directory_string)
            source = directory / "source.bam"
            upstream = directory / "upstream.bam"
            dumi = directory / "dumi.bam"
            for path, payload in (
                (source, b"source"),
                (upstream, b"upstream"),
                (dumi, b"dumi"),
            ):
                path.write_bytes(payload)
            workload = RUNNER.Workload(
                workload.name,
                workload.scale,
                workload.umi_length,
                workload.paired,
                workload.generator_args,
                external_input=RUNNER.ExternalBamInput(
                    workload_id=workload.scale,
                    bam_path=source,
                    bam_sha256=hashlib.sha256(
                        source.read_bytes()
                    ).hexdigest(),
                    paired=False,
                    umi_length=12,
                    umi_separator="_",
                    rationale="",
                ),
                streaming_on_eligible=True,
            )

            def metrics(path: Path) -> dict[str, object]:
                block: dict[str, object] = {
                    field: 0
                    for field in RUNNER.DIRECTIONAL_ORACLE_METRIC_COUNT_FIELDS
                }
                block.update(
                    {
                        field: "a" * 64
                        for field in (
                            RUNNER.DIRECTIONAL_ORACLE_METRIC_SHA256_FIELDS
                        )
                    }
                )
                block.update(
                    {
                        "input_bytes": path.stat().st_size,
                        "input_sha256": hashlib.sha256(
                            path.read_bytes()
                        ).hexdigest(),
                        "records": 1,
                        "eligible_records": 1,
                        "input_records": 1,
                        "membership_partition_bytes": 1,
                        "rooted_partition_bytes": 1,
                        "alignment_umi_frequency_multiset_bytes": 1,
                    }
                )
                return block

            receipt = {
                "schema": RUNNER.DIRECTIONAL_ORACLE_SCHEMA,
                "version": RUNNER.DIRECTIONAL_ORACLE_SCHEMA_VERSION,
                "methods": dict(RUNNER.DIRECTIONAL_ORACLE_METHODS),
                "configuration": {
                    "mode": "single-end",
                    "umi_length": 12,
                    "umi_separator_bytes": 1,
                    "umi_separator_sha256": hashlib.sha256(b"_").hexdigest(),
                    "edit_distance": 1,
                    "percentage_decimal": "0.5",
                    "percentage_binary32_hex": "3f000000",
                    "remove_unpaired": False,
                    "remove_chimeric": False,
                    "sort_buffer_size": "256M",
                },
                "gate": {
                    field: True
                    for field in RUNNER.DIRECTIONAL_ORACLE_GATE_FIELDS
                },
                "diagnostics": {
                    field: True
                    for field in RUNNER.DIRECTIONAL_ORACLE_DIAGNOSTIC_FIELDS
                },
                "source_oracle": metrics(source),
                "canonical_upstream": metrics(upstream),
                "dumi_off": metrics(dumi),
                "temporary_storage": {
                    "persistent_stage_peak_upper_bound_bytes": 1,
                    "sort_merge_storage_note": "private bounded merge scratch",
                },
                "provenance": {
                    "helper_sha256": hashlib.sha256(
                        (
                            ROOT
                            / "scripts"
                            / "benchmark"
                            / "directional_oracle_check.py"
                        ).read_bytes()
                    ).hexdigest(),
                    "partition_checker_sha256": hashlib.sha256(
                        (
                            ROOT
                            / "scripts"
                            / "benchmark"
                            / "cluster_partition_check.py"
                        ).read_bytes()
                    ).hexdigest(),
                    "private_streams_retained": False,
                },
            }
            RUNNER.validate_directional_oracle_receipt(
                receipt=receipt,
                return_code=0,
                workload=workload,
                source=source,
                canonical_upstream=upstream,
                dumi_off=dumi,
                directional_checker=(
                    ROOT
                    / "scripts"
                    / "benchmark"
                    / "directional_oracle_check.py"
                ),
                partition_checker=(
                    ROOT
                    / "scripts"
                    / "benchmark"
                    / "cluster_partition_check.py"
                ),
            )

            contradicted = json.loads(json.dumps(receipt))
            contradicted["canonical_upstream"][
                "membership_partition_sha256"
            ] = "b" * 64
            with self.assertRaisesRegex(
                RUNNER.BenchmarkError,
                "diagnostics contradict receipt evidence",
            ):
                RUNNER.validate_directional_oracle_receipt(
                    receipt=contradicted,
                    return_code=0,
                    workload=workload,
                    source=source,
                    canonical_upstream=upstream,
                    dumi_off=dumi,
                    directional_checker=(
                        ROOT
                        / "scripts"
                        / "benchmark"
                        / "directional_oracle_check.py"
                    ),
                    partition_checker=(
                        ROOT
                        / "scripts"
                        / "benchmark"
                        / "cluster_partition_check.py"
                    ),
                )

            dropped = json.loads(json.dumps(receipt))
            dropped["dumi_off"][
                "alignment_umi_frequency_multiset_sha256"
            ] = "c" * 64
            with self.assertRaisesRegex(
                RUNNER.BenchmarkError,
                "do not preserve the exact source",
            ):
                RUNNER.validate_directional_oracle_receipt(
                    receipt=dropped,
                    return_code=0,
                    workload=workload,
                    source=source,
                    canonical_upstream=upstream,
                    dumi_off=dumi,
                    directional_checker=(
                        ROOT
                        / "scripts"
                        / "benchmark"
                        / "directional_oracle_check.py"
                    ),
                    partition_checker=(
                        ROOT
                        / "scripts"
                        / "benchmark"
                        / "cluster_partition_check.py"
                    ),
                )

            inconsistent_aggregate = json.loads(json.dumps(receipt))
            inconsistent_aggregate["dumi_off"]["clusters"] = 2
            with self.assertRaisesRegex(
                RUNNER.BenchmarkError,
                "inconsistent dUMI/source aggregate metrics",
            ):
                RUNNER.validate_directional_oracle_receipt(
                    receipt=inconsistent_aggregate,
                    return_code=0,
                    workload=workload,
                    source=source,
                    canonical_upstream=upstream,
                    dumi_off=dumi,
                    directional_checker=(
                        ROOT
                        / "scripts"
                        / "benchmark"
                        / "directional_oracle_check.py"
                    ),
                    partition_checker=(
                        ROOT
                        / "scripts"
                        / "benchmark"
                        / "cluster_partition_check.py"
                    ),
                )

            def pairwise_side(
                directional: dict[str, object],
            ) -> dict[str, object]:
                return {
                    "input_records": directional["input_records"],
                    "eligible_records": directional["eligible_records"],
                    "excluded_unmapped": directional["excluded_unmapped"],
                    "excluded_second_of_pair": directional[
                        "excluded_second_of_pair"
                    ],
                    "excluded_unpaired": directional["excluded_unpaired"],
                    "excluded_mate_unmapped": directional[
                        "excluded_mate_unmapped"
                    ],
                    "excluded_chimeric": directional[
                        "excluded_chimeric"
                    ],
                    "alignment_groups": directional["alignment_groups"],
                    "clusters": directional["clusters"],
                    "umi_memberships": directional["umi_memberships"],
                    "max_umi_memberships_per_cluster": directional[
                        "max_umi_memberships_per_cluster"
                    ],
                    "record_key_bytes": directional["record_key_bytes"],
                    "canonical_partition_bytes": directional[
                        "membership_partition_bytes"
                    ],
                    "partition_cluster_multiset_sha256": directional[
                        "membership_partition_sha256"
                    ],
                    "reference_sequences": directional[
                        "reference_sequences"
                    ],
                    "reference_dictionary_sha256": directional[
                        "reference_dictionary_sha256"
                    ],
                    "read_groups": directional["read_groups"],
                    "read_group_dictionary_sha256": directional[
                        "read_group_dictionary_sha256"
                    ],
                }

            pairwise = {
                "schema": "dumi-cluster-partition-check-v1",
                "partition_fingerprint_version": (
                    "umicollapse-tag-alignment-cluster-umi-frequency-v1"
                ),
                "equivalent": True,
                "partition_equivalent": True,
                "reference_dictionary_equivalent": True,
                "read_group_dictionary_equivalent": True,
                "configuration": {
                    "mode": "single-end",
                    "umi_length": 12,
                    "umi_separator_bytes": 1,
                    "umi_separator_sha256": hashlib.sha256(b"_").hexdigest(),
                    "remove_unpaired": False,
                    "remove_chimeric": False,
                    "sort_buffer_size": "256M",
                },
                "left": pairwise_side(receipt["canonical_upstream"]),
                "right": pairwise_side(receipt["dumi_off"]),
                "temporary_storage": {
                    "persistent_stage_peak_upper_bound_bytes": 1,
                    "sort_merge_storage_note": "private",
                },
            }
            RUNNER.validate_pairwise_cluster_diagnostic_receipt(
                receipt=pairwise,
                return_code=0,
                workload=workload,
                directional_receipt=receipt,
            )
            contradicted_pairwise = json.loads(json.dumps(pairwise))
            contradicted_pairwise["left"]["clusters"] = 2
            with self.assertRaisesRegex(
                RUNNER.BenchmarkError,
                "pairwise canonical upstream metrics contradict",
            ):
                RUNNER.validate_pairwise_cluster_diagnostic_receipt(
                    receipt=contradicted_pairwise,
                    return_code=0,
                    workload=workload,
                    directional_receipt=receipt,
                )

            contradicted_header = json.loads(json.dumps(pairwise))
            contradicted_header["left"][
                "reference_dictionary_sha256"
            ] = "c" * 64
            with self.assertRaisesRegex(
                RUNNER.BenchmarkError,
                "pairwise canonical upstream metrics contradict",
            ):
                RUNNER.validate_pairwise_cluster_diagnostic_receipt(
                    receipt=contradicted_header,
                    return_code=0,
                    workload=workload,
                    directional_receipt=receipt,
                )

            contradicted_boolean = json.loads(json.dumps(pairwise))
            contradicted_boolean["partition_equivalent"] = False
            contradicted_boolean["equivalent"] = False
            with self.assertRaisesRegex(
                RUNNER.BenchmarkError,
                "pairwise diagnostic booleans contradict",
            ):
                RUNNER.validate_pairwise_cluster_diagnostic_receipt(
                    receipt=contradicted_boolean,
                    return_code=1,
                    workload=workload,
                    directional_receipt=receipt,
                )

            contradicted_directional = json.loads(json.dumps(receipt))
            contradicted_directional["diagnostics"][
                "canonical_upstream_dumi_off_partition_equivalent"
            ] = False
            with self.assertRaisesRegex(
                RUNNER.BenchmarkError,
                "pairwise diagnostic booleans contradict",
            ):
                RUNNER.validate_pairwise_cluster_diagnostic_receipt(
                    receipt=pairwise,
                    return_code=0,
                    workload=workload,
                    directional_receipt=contradicted_directional,
                )

            invalid_configuration = json.loads(json.dumps(pairwise))
            invalid_configuration["configuration"][
                "sort_buffer_size"
            ] = "1M"
            with self.assertRaisesRegex(
                RUNNER.BenchmarkError,
                "violated its contract",
            ):
                RUNNER.validate_pairwise_cluster_diagnostic_receipt(
                    receipt=invalid_configuration,
                    return_code=0,
                    workload=workload,
                    directional_receipt=receipt,
                )

    def test_directional_gate_orchestration_keeps_pairwise_diagnostic(
        self,
    ) -> None:
        workload = external_workload(eligible=True)
        with tempfile.TemporaryDirectory() as directory_string:
            directory = Path(directory_string)
            source = directory / "input.private.bam"
            source.write_bytes(b"private-source")
            private_root = directory / "private-gate"
            directional_receipt_path = directory / "directional.json"
            pairwise_receipt_path = directory / "pairwise.json"

            def fake_build(**kwargs: object) -> list[str]:
                self.assertTrue(kwargs["tag_clusters"])
                Path(kwargs["output"]).write_bytes(b"private-tagged")
                return ["fake-java", str(kwargs["source_key"])]

            def fake_run(
                command: object, **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                arguments = [str(value) for value in command]
                if len(arguments) > 1 and arguments[1].endswith(
                    "directional_oracle_check.py"
                ):
                    self.assertEqual(
                        arguments[arguments.index("--edit-distance") + 1],
                        "1",
                    )
                    self.assertEqual(
                        arguments[arguments.index("--percentage") + 1],
                        "0.5",
                    )
                    destination = Path(
                        arguments[arguments.index("--receipt") + 1]
                    )
                    destination.write_text(
                        json.dumps(
                            {
                                "gate": {
                                    "directional_oracle_gate_pass": True
                                }
                            }
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    return subprocess.CompletedProcess(
                        arguments, 0, "", ""
                    )
                if len(arguments) > 1 and arguments[1].endswith(
                    "cluster_partition_check.py"
                ):
                    destination = Path(
                        arguments[arguments.index("--receipt") + 1]
                    )
                    destination.write_text(
                        json.dumps({"equivalent": False}) + "\n",
                        encoding="utf-8",
                    )
                    return subprocess.CompletedProcess(
                        arguments, 1, "", ""
                    )
                return subprocess.CompletedProcess(arguments, 0, "", "")

            with (
                mock.patch.object(
                    RUNNER,
                    "build_java_bam_command",
                    side_effect=fake_build,
                ),
                mock.patch.object(
                    RUNNER, "run_command", side_effect=fake_run
                ),
                mock.patch.object(
                    RUNNER, "validate_directional_oracle_receipt"
                ) as validate_directional,
                mock.patch.object(
                    RUNNER,
                    "validate_pairwise_cluster_diagnostic_receipt",
                ) as validate_pairwise,
            ):
                observed = RUNNER.run_external_directional_oracle_gate(
                    workload=workload,
                    bam_input=source,
                    private_root=private_root,
                    directional_receipt_path=directional_receipt_path,
                    pairwise_receipt_path=pairwise_receipt_path,
                    java=Path("/fake/java"),
                    jvm_options=(),
                    classes={
                        "upstream": Path("/classes/upstream"),
                        "dumi": Path("/classes/dumi"),
                    },
                    common_classpath="/dependencies",
                    python=Path("/fake/python"),
                    directional_checker=Path(
                        "/fake/directional_oracle_check.py"
                    ),
                    pairwise_checker=Path(
                        "/fake/cluster_partition_check.py"
                    ),
                    samtools=Path("/fake/samtools"),
                    sort_command=Path("/fake/gsort"),
                )
            self.assertTrue(
                observed["directional"]["gate"][
                    "directional_oracle_gate_pass"
                ]
            )
            self.assertFalse(observed["pairwise"]["equivalent"])
            self.assertFalse(private_root.exists())
            validate_directional.assert_called_once()
            validate_pairwise.assert_called_once()

    def test_auto_fallback_is_a_valid_observed_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory_string:
            directory = Path(directory_string)
            stdout_path = directory / "stdout.txt"
            stderr_path = directory / "stderr.txt"
            stdout_path.write_text(RUNNER.STREAMING_MARKER + "\n", encoding="utf-8")
            stderr_path.write_text(
                RUNNER.STREAMING_FALLBACK_MARKER + ": details omitted\n",
                encoding="utf-8",
            )
            self.assertEqual(
                RUNNER.observed_execution_route(
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    sort_order="coordinate",
                    implementation_name="dumi",
                    requested_mode="auto",
                    paired=False,
                    context="test",
                ),
                "fallback-off",
            )

    def test_auto_route_must_agree_with_forced_on_eligibility(self) -> None:
        with self.assertRaisesRegex(
            RUNNER.BenchmarkError, "contradicts the forced-on eligibility"
        ):
            RUNNER.validate_external_route_contract(
                workload=external_workload(eligible=True),
                implementation_name="dumi",
                requested_mode="auto",
                observed_route="off-ineligible",
                context="test",
            )
        RUNNER.validate_external_route_contract(
            workload=external_workload(eligible=True),
            implementation_name="dumi",
            requested_mode="auto",
            observed_route="fallback-off",
            context="test",
        )

    def test_cross_implementation_differences_are_diagnostic(self) -> None:
        receipt = RUNNER.cross_implementation_oracle_receipt(
            candidate=self.oracle_receipt(exact=False),
            reference=self.oracle_receipt(exact=True),
            context="test",
        )
        self.assertFalse(receipt["exact_match"])
        self.assertTrue(receipt["output_count_match"])
        self.assertTrue(receipt["alignment_group_output_count_match"])

        different = RUNNER.cross_implementation_oracle_receipt(
            candidate=self.oracle_receipt(
                exact=False,
                groups_equal=False,
                output_records=9,
            ),
            reference=self.oracle_receipt(exact=True),
            context="test",
        )
        self.assertEqual(different["status"], "difference")
        self.assertEqual(different["scope"], "diagnostic-only")
        self.assertFalse(different["output_count_match"])
        self.assertFalse(
            different["alignment_group_output_count_match"]
        )

    def test_private_snapshot_is_an_independent_verified_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory_string:
            directory = Path(directory_string)
            source = directory / "source.bam"
            source.write_bytes(b"private-bam-placeholder")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            entry = RUNNER.ExternalBamInput(
                workload_id="demo-se-01",
                bam_path=source,
                bam_sha256=digest,
                paired=False,
                umi_length=12,
                umi_separator="_",
                rationale="",
            )
            snapshot, receipt = RUNNER.snapshot_external_input(
                entry=entry,
                validation_receipt={},
                destination_root=directory / "private",
            )
            self.assertEqual(snapshot.read_bytes(), source.read_bytes())
            self.assertNotEqual(snapshot.stat().st_ino, source.stat().st_ino)
            self.assertEqual(stat.S_IMODE(snapshot.stat().st_mode), 0o400)
            self.assertEqual(receipt["sha256"], digest)
            source.write_bytes(b"changed-source")
            self.assertEqual(snapshot.read_bytes(), b"private-bam-placeholder")

    def test_private_paired_index_is_rehashed_after_timing(self) -> None:
        with tempfile.TemporaryDirectory() as directory_string:
            directory = Path(directory_string)
            source = directory / "source.bam"
            source.write_bytes(b"private-bam-placeholder")
            source_index = Path(str(source) + ".bai")
            source_index.write_bytes(b"private-index-placeholder")
            bam_digest = hashlib.sha256(source.read_bytes()).hexdigest()
            index_digest = hashlib.sha256(source_index.read_bytes()).hexdigest()
            entry = RUNNER.ExternalBamInput(
                workload_id="demo-pe-01",
                bam_path=source,
                bam_sha256=bam_digest,
                paired=True,
                umi_length=12,
                umi_separator="_",
                rationale="",
            )
            validation_receipt = {
                "bytes": source.stat().st_size,
                "paired_index": {
                    "bytes": source_index.stat().st_size,
                    "sha256": index_digest,
                },
            }
            snapshot, snapshot_receipt = RUNNER.snapshot_external_input(
                entry=entry,
                validation_receipt=validation_receipt,
                destination_root=directory / "private",
            )
            snapshot_receipt["timing_index"] = dict(
                snapshot_receipt["paired_index"]
            )
            validation_receipt["private_timing_snapshot"] = snapshot_receipt
            RUNNER.verify_external_timing_snapshot(
                entry=entry,
                snapshot_bam=snapshot,
                validation_receipt=validation_receipt,
            )
            private_index = Path(str(snapshot) + ".bai")
            os_mode = stat.S_IMODE(private_index.stat().st_mode)
            self.assertEqual(os_mode, 0o400)
            private_index.chmod(0o600)
            private_index.write_bytes(b"mutated-private-index")
            with self.assertRaisesRegex(
                RUNNER.BenchmarkError, "private BAM timing index changed"
            ):
                RUNNER.verify_external_timing_snapshot(
                    entry=entry,
                    snapshot_bam=snapshot,
                    validation_receipt=validation_receipt,
                )

    def test_paired_forced_on_arbitrary_failure_is_not_ineligibility(self) -> None:
        entry = RUNNER.ExternalBamInput(
            workload_id="demo-pe-01",
            bam_path=Path("/not-opened/input.bam"),
            bam_sha256="0" * 64,
            paired=True,
            umi_length=12,
            umi_separator="_",
            rationale="",
        )
        workload = RUNNER.Workload(
            "external",
            entry.workload_id,
            entry.umi_length,
            entry.paired,
            (),
            external_input=entry,
        )
        with tempfile.TemporaryDirectory() as directory_string:
            with self.assertRaisesRegex(
                RUNNER.BenchmarkError, "failed unexpectedly"
            ):
                RUNNER.probe_forced_streaming_contract(
                    workload=workload,
                    bam_input=entry.bam_path,
                    root=Path(directory_string) / "probe",
                    java=Path("/bin/false"),
                    jvm_options=(),
                    classes_root=Path("/unused/classes"),
                    common_classpath="",
                    samtools=Path("/bin/true"),
                )


    def test_record_scan_kills_and_waits_for_child_on_interrupt(self) -> None:
        class InterruptingStream:
            def __init__(self) -> None:
                self.closed = False

            def __iter__(self) -> object:
                return self

            def __next__(self) -> bytes:
                raise RUNNER.BenchmarkSignalInterrupt(signal.SIGTERM)

            def close(self) -> None:
                self.closed = True

        class FakeProcess:
            def __init__(self) -> None:
                self.stdout = InterruptingStream()
                self.killed = False
                self.waited = False

            def poll(self) -> int | None:
                return -signal.SIGKILL if self.waited else None

            def kill(self) -> None:
                self.killed = True

            def wait(self) -> int:
                self.waited = True
                return -signal.SIGKILL

        workload = external_workload(eligible=True)
        assert workload.external_input is not None
        fake_process = FakeProcess()
        with tempfile.TemporaryDirectory() as directory_string:
            validation_root = Path(directory_string)
            with (
                mock.patch.object(
                    RUNNER.subprocess,
                    "Popen",
                    return_value=fake_process,
                ),
                self.assertRaises(RUNNER.BenchmarkSignalInterrupt),
            ):
                RUNNER.validate_external_records(
                    entry=workload.external_input,
                    samtools=Path("/fake/samtools"),
                    validation_root=validation_root,
                )
        self.assertTrue(fake_process.stdout.closed)
        self.assertTrue(fake_process.killed)
        self.assertTrue(fake_process.waited)

    def test_directional_oracle_annotations_cover_measured_stages(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory_string:
            directory = Path(directory_string)
            measurement_path = directory / "measurements.tsv"
            rows = []
            for stage in RUNNER.MEASURED_STAGES:
                row = {
                    field: "" for field in RUNNER.MEASUREMENT_COLUMNS
                }
                row.update(
                    {
                        "run_id": f"external-demo-{stage}",
                        "workload": "external",
                        "scale": "demo-se-01",
                        "stage": stage,
                    }
                )
                rows.append(row)
            with measurement_path.open(
                "w", encoding="utf-8", newline=""
            ) as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=RUNNER.MEASUREMENT_COLUMNS,
                    delimiter="\t",
                    lineterminator="\n",
                )
                writer.writeheader()
                writer.writerows(rows)
            RUNNER.annotate_external_directional_oracle_measurements(
                measurement_path=measurement_path,
                results={
                    "demo-se-01": (
                        {
                            "gate": {
                                "directional_oracle_gate_pass": True,
                                "dumi_off_oracle_partition_equivalent": True,
                                "dumi_off_oracle_root_assignment_equivalent": True,
                            },
                            "diagnostics": {
                                "canonical_upstream_oracle_partition_equivalent": False,
                                "canonical_upstream_oracle_root_assignment_equivalent": False,
                                "canonical_upstream_dumi_off_partition_equivalent": False,
                                "canonical_upstream_dumi_off_root_assignment_equivalent": False,
                            },
                        },
                        (
                            "oracles/external/demo-se-01/"
                            "directional-oracle-receipt.json"
                        ),
                    )
                },
            )
            with measurement_path.open(
                "r", encoding="utf-8", newline=""
            ) as stream:
                observed = list(
                    csv.DictReader(stream, delimiter="\t")
                )
            self.assertEqual(
                {
                    row["directional_oracle_gate_pass"]
                    for row in observed
                },
                {"True"},
            )
            self.assertEqual(
                {
                    row[
                        "canonical_upstream_oracle_partition_equivalent"
                    ]
                    for row in observed
                },
                {"False"},
            )
            self.assertEqual(
                {row["stage"] for row in observed},
                set(RUNNER.MEASURED_STAGES),
            )
            self.assertEqual(
                {
                    row["directional_oracle_receipt"]
                    for row in observed
                },
                {
                    "oracles/external/demo-se-01/"
                    "directional-oracle-receipt.json"
                },
            )

    def test_early_failure_logs_are_suppressed(self) -> None:
        with tempfile.TemporaryDirectory() as directory_string:
            directory = Path(directory_string)
            stderr_path = directory / "records-stderr.txt"
            stderr_path.write_text(
                "sensitive-qname-and-path\n", encoding="utf-8"
            )
            RUNNER.suppress_external_log_contents(directory)
            redacted = stderr_path.read_text(encoding="utf-8")
            self.assertIn("suppressed after validation", redacted)
            self.assertNotIn("sensitive-qname-and-path", redacted)

    def test_sigterm_kills_timed_process_tree_before_private_cleanup(
        self,
    ) -> None:
        program = r"""
import importlib.util
from pathlib import Path
import signal
import subprocess
import sys
import time

runner_path = Path(sys.argv[1])
output_root = Path(sys.argv[2])
gnu_time = Path(sys.argv[3])
spec = importlib.util.spec_from_file_location("signal_test_runner", runner_path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

def sleeper():
    module.ACTIVE_OUTPUT_ROOT = output_root
    module.ACTIVE_EXTERNAL_INPUT_MODE = True
    private_root = output_root / "private-inputs" / "sample"
    log_root = output_root / "runs" / "cell"
    private_root.mkdir(parents=True)
    log_root.mkdir(parents=True)
    (private_root / "input.private.bam").write_bytes(b"private")
    (log_root / "stderr.txt").write_text("private-qname\n", encoding="utf-8")
    (output_root / "READY").write_text("ready\n", encoding="utf-8")
    grandchild_program = (
        "from pathlib import Path; import os, signal, sys, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8'); "
        "time.sleep(1.5); "
        "Path(sys.argv[2]).write_text('survived\\n', encoding='utf-8')"
    )
    wrapper_program = (
        "import subprocess, sys, time; "
        "subprocess.Popen(["
        "'/bin/bash', '-c', "
        "'\"$1\" -c \"$2\" \"$3\" \"$4\"', "
        "'benchmark-descendant', sys.executable, sys.argv[1], "
        "sys.argv[2], sys.argv[3]"
        "]); "
        "time.sleep(30)"
    )
    try:
        module.run_command(
            [
                gnu_time,
                "--format=elapsed=%e",
                sys.executable,
                "-c",
                wrapper_program,
                grandchild_program,
                output_root / "DESCENDANT_PID",
                output_root / "DESCENDANT_SURVIVED",
            ],
            check=False,
        )
    finally:
        (output_root / "INNER_CLEANUP_STARTED").write_text(
            "started\n", encoding="utf-8"
        )
        time.sleep(0.5)
        (output_root / "INNER_CLEANUP_FINISHED").write_text(
            "finished\n", encoding="utf-8"
        )
    return 0

raise SystemExit(module.cli_entrypoint(sleeper))
"""
        with tempfile.TemporaryDirectory() as directory_string:
            output_root = Path(directory_string) / "evidence"
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    program,
                    str(RUNNER_PATH),
                    str(output_root),
                    str(RUNNER.find_gnu_time(None)),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            deadline = time.monotonic() + 10
            while (
                (
                    not (output_root / "READY").is_file()
                    or not (output_root / "DESCENDANT_PID").is_file()
                )
                and process.poll() is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.05)
            self.assertTrue((output_root / "READY").is_file())
            self.assertTrue((output_root / "DESCENDANT_PID").is_file())
            descendant_pid = int(
                (output_root / "DESCENDANT_PID").read_text(encoding="utf-8")
            )
            process.send_signal(signal.SIGTERM)
            cleanup_deadline = time.monotonic() + 5
            while (
                not (output_root / "INNER_CLEANUP_STARTED").is_file()
                and process.poll() is None
                and time.monotonic() < cleanup_deadline
            ):
                time.sleep(0.01)
            self.assertTrue(
                (output_root / "INNER_CLEANUP_STARTED").is_file()
            )
            process.send_signal(signal.SIGHUP)
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 128 + signal.SIGTERM)
            self.assertEqual(stdout, "")
            self.assertIn("interrupted by SIGTERM", stderr)
            self.assertFalse(
                (
                    output_root
                    / "private-inputs"
                    / "sample"
                    / "input.private.bam"
                ).exists()
            )
            redacted = (
                output_root / "runs" / "cell" / "stderr.txt"
            ).read_text(encoding="utf-8")
            self.assertIn("suppressed after validation", redacted)
            status = json.loads(
                (output_root / "STATUS.json").read_text(encoding="utf-8")
            )
            self.assertEqual(status["state"], "FAILED")
            self.assertEqual(status["detail"], "interrupted by SIGTERM")
            self.assertTrue(
                (output_root / "INNER_CLEANUP_FINISHED").is_file()
            )
            time.sleep(0.75)
            self.assertFalse((output_root / "DESCENDANT_SURVIVED").exists())
            with self.assertRaises(ProcessLookupError):
                os.kill(descendant_pid, 0)


if __name__ == "__main__":
    unittest.main()
