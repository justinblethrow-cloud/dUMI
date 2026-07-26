#!/usr/bin/env python3
"""Adversarial contracts for the external-evidence public exporter."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import csv
from functools import lru_cache
import hashlib
import importlib.util
import json
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
EXPORTER_PATH = ROOT / "scripts" / "benchmark" / "export_public_external.py"
SPEC = importlib.util.spec_from_file_location(
    "dumi_public_external_exporter", EXPORTER_PATH
)
assert SPEC is not None and SPEC.loader is not None
EXPORTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = EXPORTER
SPEC.loader.exec_module(EXPORTER)

SOURCE_ID = "source-a"
PUBLIC_ALIAS = "panel-se-01"
PRIVATE_TOKEN = "private-order-token"
PRIVATE_BRAND = "ConfidentialSequencingCo"
PRIVATE_PATH = "/srv/private/source-a.bam"
PRIVATE_BAM_SHA256 = "a" * 64
PRIVATE_BAI_SHA256 = "b" * 64
PRIVATE_SEMANTIC_SHA256 = "c" * 64
PRIVATE_OUTPUT_SHA256 = "d" * 64
PRIVATE_REFERENCE_SHA256 = "e" * 64
PRIVATE_ALTERNATE_SEMANTIC_SHA256 = "f" * 64
PRIVATE_ALTERNATE_OUTPUT_SHA256 = "9" * 64
PRIVATE_PROVENANCE_LEDGER_SHA256 = "7" * 64
PUBLIC_DEPENDENCY_SHA256 = EXPORTER.PUBLIC_DEPENDENCY_SHA256[
    "htsjdk-3.0.5.jar"
]
PUBLIC_SNAPPY_SHA256 = EXPORTER.PUBLIC_DEPENDENCY_SHA256[
    "snappy-java-1.1.10.8.jar"
]
CANONICAL_SHA = EXPORTER.CANONICAL_SOURCE_SHA
DUMI_SHA = subprocess.run(
    ["git", "-C", os.fspath(ROOT), "rev-parse", "HEAD"],
    check=True,
    capture_output=True,
    text=True,
).stdout.strip()
EVIDENCE_SET_ID = "external-evidence-fixture"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    path.chmod(0o600)


def write_tsv(
    path: Path,
    fields: tuple[str, ...],
    rows: list[dict[str, str]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=fields,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
    path.chmod(0o600)


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def load_module(name: str, path: Path) -> object:
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    benchmark_directory = str(path.parent)
    sys.path.insert(0, benchmark_directory)
    try:
        specification.loader.exec_module(module)
    finally:
        sys.path.remove(benchmark_directory)
    return module


@lru_cache(maxsize=4)
def committed_tree_files(commit: str, prefix: str) -> dict[str, bytes]:
    listing = subprocess.run(
        [
            "git",
            "-C",
            os.fspath(ROOT),
            "ls-tree",
            "-r",
            "-z",
            commit,
            "--",
            prefix,
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    expected_prefix = prefix.rstrip("/") + "/"
    output: dict[str, bytes] = {}
    for record in (item for item in listing.split(b"\0") if item):
        metadata, raw_path = record.split(b"\t", 1)
        _mode, kind, object_id = metadata.decode("ascii").split()
        path = raw_path.decode("utf-8")
        if kind != "blob" or not path.startswith(expected_prefix):
            raise AssertionError("test Git source inventory is invalid")
        relative = path[len(expected_prefix) :]
        output[relative] = subprocess.run(
            [
                "git",
                "-C",
                os.fspath(ROOT),
                "cat-file",
                "blob",
                object_id,
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    if not output:
        raise AssertionError("test Git source inventory is empty")
    return output


class RestrictedBundleFixture:
    def __init__(self, root: Path, *, paired: bool = False) -> None:
        self.root = root
        self.paired = paired
        self.bundle = root / "restricted"
        self.output = root / "public"
        self.alias_map = root / "aliases.private.json"
        self.denylist = root / "denylist.private.json"
        self.private_receipt = root / "export.private.json"
        self.bundle.mkdir(mode=0o700)
        self._write_bundle()
        self.write_aliases()
        self.write_denylist()

    def treatments(self) -> tuple[dict[str, str], ...]:
        treatments = (
            {
                "implementation": "canonical-upstream",
                "mode": "legacy",
                "order": "1",
                "route": "coordinate",
                "elapsed": "2.0",
                "rss": "100000",
            },
            {
                "implementation": "dumi",
                "mode": "off",
                "order": "2",
                "route": "off",
                "elapsed": "1.2",
                "rss": "70000",
            },
            {
                "implementation": "dumi",
                "mode": "on",
                "order": "4",
                "route": "streaming",
                "elapsed": "1.1",
                "rss": "60000",
            },
            {
                "implementation": "dumi",
                "mode": "auto",
                "order": "3",
                "route": "streaming",
                "elapsed": "1.0",
                "rss": "50000",
            },
        )
        if self.paired:
            return tuple(
                {
                    **treatment,
                    "order": (
                        "3"
                        if treatment["mode"] == "auto"
                        else treatment["order"]
                    ),
                    "route": (
                        "off-ineligible"
                        if treatment["mode"] == "auto"
                        else treatment["route"]
                    ),
                }
                for treatment in treatments
                if treatment["mode"] != "on"
            )
        return treatments

    def _write_bundle(self) -> None:
        pairing_mode = "paired" if self.paired else "single-end"
        alignment_records = 500 if self.paired else 1000
        excluded_second = 500 if self.paired else 0
        streaming_eligible = not self.paired
        source_paired_index = (
            {
                "bytes": 100,
                "sha256": PRIVATE_BAI_SHA256,
                "path_recorded": False,
                "validation": "pass",
            }
            if self.paired
            else None
        )
        snapshot_paired_index = (
            {
                "bytes": 100,
                "sha256": PRIVATE_BAI_SHA256,
                "format": "bai",
                "path_recorded": False,
            }
            if self.paired
            else None
        )
        forced_on_contract = (
            {
                "status": "pass",
                "eligible": False,
                "timed_cell_scheduled": False,
                "observed_route": "rejected-ineligible",
                "exit_code": 2,
                "output_created": False,
                "streaming_marker_seen": False,
                "fallback_marker_seen": False,
                "observed_sort_order": None,
                "rejection_reason": "paired-mode-incompatible",
                "logs_suppressed": True,
            }
            if self.paired
            else {
                "status": "pass",
                "eligible": True,
                "timed_cell_scheduled": True,
                "observed_route": "streaming",
                "exit_code": 0,
                "output_created": True,
                "streaming_marker_seen": True,
                "fallback_marker_seen": False,
                "observed_sort_order": "unsorted",
                "rejection_reason": None,
                "logs_suppressed": True,
            }
        )
        dependency_path = self.bundle / "dependencies" / "htsjdk-3.0.5.jar"
        dependency_path.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "lib" / dependency_path.name, dependency_path)
        dependency_path.chmod(0o600)
        snappy_path = (
            self.bundle
            / "dependencies"
            / "snappy-java-1.1.10.8.jar"
        )
        shutil.copyfile(ROOT / "lib" / snappy_path.name, snappy_path)
        snappy_path.chmod(0o600)

        harness_files: list[dict[str, str]] = []
        for relative in EXPORTER.HARNESS_PATHS:
            path = self.bundle.joinpath(*Path(relative).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"neutral fixture for {path.name}\n", encoding="utf-8"
            )
            path.chmod(0o600)
            repository_path = (
                self.bundle
                / "sources"
                / "dumi"
                / "scripts"
                / "benchmark"
                / path.name
            )
            repository_path.parent.mkdir(parents=True, exist_ok=True)
            repository_path.write_bytes(path.read_bytes())
            repository_path.chmod(0o600)
            harness_files.append(
                {"path": relative, "sha256": sha256(path)}
            )

        dependency_lock = (
            self.bundle / "sources" / "dumi" / "dependencies.lock"
        )
        dependency_lock.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "dependencies.lock", dependency_lock)
        dependency_lock.chmod(0o600)

        builds: dict[str, dict[str, object]] = {}
        source_commits = {
            "upstream": CANONICAL_SHA,
            "dumi": DUMI_SHA,
        }
        for label in ("upstream", "dumi"):
            source_root = (
                self.bundle
                / "sources"
                / label
                / "src"
                / "umicollapse"
            )
            source_root.mkdir(parents=True)
            for relative, payload in committed_tree_files(
                source_commits[label], "src/umicollapse"
            ).items():
                source = source_root.joinpath(*Path(relative).parts)
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(payload)
                source.chmod(0o600)
            classes_root = self.bundle / "classes" / label
            classes_root.mkdir(parents=True)
            compiled = classes_root / "Example.class"
            compiled.write_bytes(b"\xca\xfe\xba\xbe fixture " + label.encode())
            compiled.chmod(0o600)
            command = (
                self.bundle
                / "build-commands"
                / label
                / "command.txt"
            )
            command.parent.mkdir(parents=True)
            sources = sorted(source_root.rglob("*.java"))
            command.write_text(
                EXPORTER.expected_build_command(
                    label, sources, source_root
                ),
                encoding="utf-8",
            )
            command.chmod(0o600)
            builds[label] = {
                "label": label,
                "source_tree_sha256": EXPORTER.sha256_tree(source_root),
                "classes_tree_sha256": EXPORTER.sha256_tree(classes_root),
                "source_count": len(sources),
                "command_file": f"build-commands/{label}/command.txt",
            }

        input_bytes = 540
        treatment_count = len(self.treatments())
        estimated_output_bytes = (input_bytes * 5 + 3) // 4
        retained_allowances = treatment_count * 2
        sort_allowances = 1
        timing_peak_bytes = estimated_output_bytes * (
            retained_allowances + sort_allowances
        )
        directional_records = 10
        alignment_key_bytes = 25 if self.paired else 17
        source_record_key_bytes = directional_records * (
            alignment_key_bytes + 12 + 2
        )
        tagged_record_key_bytes = directional_records * (
            alignment_key_bytes + 2 * 12 + 12
        )
        membership_bytes = directional_records * (
            alignment_key_bytes + 12 + 13
        )
        rooted_bytes = directional_records * (
            alignment_key_bytes + 2 * 12 + 14
        )
        alignment_umi_aggregate_bytes = membership_bytes
        retained_canonical_bytes = 3 * (
            membership_bytes + rooted_bytes
        )
        active_persistent_bytes = (
            tagged_record_key_bytes
            + retained_canonical_bytes
            + alignment_umi_aggregate_bytes
        )
        concurrent_destination_merge_bytes = 2 * (
            membership_bytes
            + rooted_bytes
            + alignment_umi_aggregate_bytes
        )
        destination_merge_bytes = (
            membership_bytes
            + rooted_bytes
            + alignment_umi_aggregate_bytes
        )
        concurrent_sort_buffer_bytes = 3 * 256 * 1024 * 1024
        tagged_bam_bytes = max(
            estimated_output_bytes + directional_records * (12 + 32),
            input_bytes + directional_records * (2 * 12 + 64),
        )
        tagged_bam_allowance_bytes = 2 * tagged_bam_bytes
        directional_peak_bytes = (
            tagged_bam_allowance_bytes
            + active_persistent_bytes
            + destination_merge_bytes
        )
        peak_bytes = max(timing_peak_bytes, directional_peak_bytes)
        headroom_bytes = max(
            256 * 1024 * 1024, (peak_bytes + 9) // 10
        )
        required_bytes = peak_bytes + headroom_bytes
        capacity_common = {
            "status": "pass",
            "treatments_per_repetition_block": treatment_count,
            "retained_block_output_allowances": retained_allowances,
            "samtools_sort_scratch_allowances": sort_allowances,
            "input_bam_bytes": input_bytes,
            "estimated_output_bytes_per_cell": estimated_output_bytes,
            "timing_peak_stage_bytes": timing_peak_bytes,
            "directional_oracle_applicable": True,
            "directional_oracle_record_count_upper_bound": (
                directional_records
            ),
            "directional_oracle_alignment_key_bytes_per_record": (
                alignment_key_bytes
            ),
            "directional_oracle_source_record_key_bytes": (
                source_record_key_bytes
            ),
            "directional_oracle_tagged_record_key_bytes_each": (
                tagged_record_key_bytes
            ),
            "directional_oracle_membership_canonical_bytes_each": (
                membership_bytes
            ),
            "directional_oracle_rooted_canonical_bytes_each": rooted_bytes,
            "directional_oracle_alignment_umi_aggregate_bytes_each": (
                alignment_umi_aggregate_bytes
            ),
            "directional_oracle_retained_canonical_bytes": (
                retained_canonical_bytes
            ),
            "directional_oracle_active_persistent_bytes": (
                active_persistent_bytes
            ),
            "directional_oracle_concurrent_sort_destination_merge_bytes": (
                concurrent_destination_merge_bytes
            ),
            "directional_oracle_sort_destination_merge_bytes": (
                destination_merge_bytes
            ),
            "directional_oracle_concurrent_sort_buffer_memory_bytes": (
                concurrent_sort_buffer_bytes
            ),
            "directional_oracle_tagged_bam_bytes_each": tagged_bam_bytes,
            "directional_oracle_tagged_bam_allowance_bytes": (
                tagged_bam_allowance_bytes
            ),
            "directional_oracle_peak_stage_bytes": directional_peak_bytes,
        }
        capacity_receipt = (
            self.bundle
            / "inputs"
            / "external"
            / SOURCE_ID
            / "stage-scratch-capacity.json"
        )
        capacity_receipt.parent.mkdir(parents=True)
        write_json(
            capacity_receipt,
            {
                **capacity_common,
                "scope": (
                    "complete-timing-block-and-deferred-directional-oracle"
                ),
                "peak_stage_output_bytes": peak_bytes,
                "headroom_bytes": headroom_bytes,
                "required_available_bytes": required_bytes,
                "available_bytes": required_bytes + 1024,
            },
        )
        post_capacity_receipt = (
            capacity_receipt.parent
            / "post-timing-oracle-scratch-capacity.json"
        )
        post_headroom_bytes = max(
            256 * 1024 * 1024,
            (directional_peak_bytes + 9) // 10,
        )
        post_required_bytes = directional_peak_bytes + post_headroom_bytes
        write_json(
            post_capacity_receipt,
            {
                **capacity_common,
                "scope": "deferred-directional-oracle-only",
                "peak_stage_output_bytes": directional_peak_bytes,
                "headroom_bytes": post_headroom_bytes,
                "required_available_bytes": post_required_bytes,
                "available_bytes": post_required_bytes + 1024,
            },
        )
        input_hash_receipt = capacity_receipt.parent / "hashes.json"
        cross_gate = {
            "alignment_group_output_count_match": True,
            "alignment_group_output_count_multiset_equal": True,
            "alignment_group_output_record_counts_equal": True,
            "exact_match": True,
            "output_count_match": True,
            "excluded_second_of_pair_counts_equal": True,
            "excluded_unmapped_counts_equal": True,
            "ordered_rg_equal": True,
            "ordered_sq_equal": True,
            "record_counts_equal": True,
            "scope": "diagnostic-only",
            "status": "match",
        }
        oracle_common = {
            "kind": "untimed_exact_implementation_oracle",
            "timed": False,
            "output_retained": False,
            "output_records": 1000,
            "semantic_sha256": PRIVATE_SEMANTIC_SHA256,
            "reference_sequences": 2,
            "reference_dictionary_sha256": PRIVATE_REFERENCE_SHA256,
        }
        write_json(
            input_hash_receipt,
            {
                "input_mode": "external_bam",
                "bam": {
                    "bytes": input_bytes,
                    "sha256": PRIVATE_BAM_SHA256,
                    "path_recorded": False,
                },
                "validation": {
                    "quickcheck_status": "pass",
                    "declared_sort_order": "coordinate",
                    "temporary_index_validation": "pass",
                },
                "workload_id": SOURCE_ID,
                "paired": self.paired,
                "umi_length": 12,
                "umi_separator": "_",
                "rationale_provided": False,
                "oracles": {
                    "dumi": {
                        **oracle_common,
                        "implementation": "dumi",
                        "mode": "off",
                        "source_sha": DUMI_SHA,
                    },
                    "canonical_upstream": {
                        **oracle_common,
                        "implementation": "canonical-upstream",
                        "source_sha": CANONICAL_SHA,
                    },
                },
                "cross_implementation_diagnostic": cross_gate,
            },
        )
        oracle_root = (
            self.bundle / "oracles" / "external" / SOURCE_ID
        )
        dumi_oracle_root = oracle_root / "dumi-off"
        canonical_oracle_root = oracle_root / "canonical-upstream"
        dumi_oracle_root.mkdir(parents=True)
        canonical_oracle_root.mkdir(parents=True)
        retained_oracle_common = {
            "quickcheck": True,
            "quickcheck_status": "pass",
            "output_records": 1000,
            "semantic_sha256": PRIVATE_SEMANTIC_SHA256,
            "sort_order": "coordinate",
            "reference_sequences": 2,
            "reference_dictionary_sha256": PRIVATE_REFERENCE_SHA256,
            "read_groups": 0,
            "read_group_dictionary_sha256": PRIVATE_OUTPUT_SHA256,
            "alignment_group_fingerprint_version": (
                EXPORTER.ALIGNMENT_GROUP_FINGERPRINT_VERSION
            ),
            "alignment_group_mode": pairing_mode,
            "alignment_group_output_records": alignment_records,
            "alignment_group_records_excluded_unmapped": 0,
            "alignment_group_records_excluded_second_of_pair": (
                excluded_second
            ),
            "alignment_group_output_count_sha256": PRIVATE_OUTPUT_SHA256,
            "output_bytes": 50000,
            "output_sha256": PRIVATE_OUTPUT_SHA256,
        }
        write_json(
            dumi_oracle_root / "inspection.json",
            {
                **retained_oracle_common,
                "output_file": (
                    f"oracles/external/{SOURCE_ID}/"
                    "dumi-off/output.private.bam"
                ),
                "expected_reference_sequences": None,
                "expected_reference_dictionary_sha256": "",
                "expected_read_groups": None,
                "expected_read_group_dictionary_sha256": "",
                (
                    "alignment_group_output_count_"
                    "reused_from_exact_reference"
                ): False,
                "reference_file": "",
                "reference_file_sha256": "",
                "reference_canonical_sha256": "",
                "reference_canonical_sha256_verified": None,
                "reference_cache_receipt_verified": None,
                "reference_cache_receipt_sha256": "",
                "reference_alignment_group_output_records": None,
                (
                    "reference_alignment_group_records_"
                    "excluded_unmapped"
                ): None,
                (
                    "reference_alignment_group_records_"
                    "excluded_second_of_pair"
                ): None,
                (
                    "reference_alignment_group_"
                    "output_count_sha256"
                ): "",
                "record_equivalent": None,
                "reference_dictionary_equivalent": None,
                "read_group_dictionary_equivalent": None,
                "alignment_group_output_count_equivalent": None,
                "exact_oracle_match": True,
            },
        )
        write_json(
            canonical_oracle_root / "inspection.json",
            {
                **retained_oracle_common,
                "output_file": (
                    f"oracles/external/{SOURCE_ID}/"
                    "canonical-upstream/output.private.bam"
                ),
                "expected_reference_sequences": 2,
                "expected_reference_dictionary_sha256": (
                    PRIVATE_REFERENCE_SHA256
                ),
                "expected_read_groups": 0,
                "expected_read_group_dictionary_sha256": (
                    PRIVATE_OUTPUT_SHA256
                ),
                (
                    "alignment_group_output_count_"
                    "reused_from_exact_reference"
                ): True,
                "reference_file": (
                    f"<EVIDENCE_DIR>/oracles/external/{SOURCE_ID}/"
                    "dumi-off/output.private.bam"
                ),
                "reference_file_sha256": PRIVATE_OUTPUT_SHA256,
                "reference_canonical_sha256": PRIVATE_SEMANTIC_SHA256,
                "reference_canonical_sha256_verified": True,
                "reference_cache_receipt_verified": True,
                "reference_cache_receipt_sha256": PRIVATE_BAI_SHA256,
                "reference_alignment_group_output_records": (
                    alignment_records
                ),
                (
                    "reference_alignment_group_records_"
                    "excluded_unmapped"
                ): 0,
                (
                    "reference_alignment_group_records_"
                    "excluded_second_of_pair"
                ): excluded_second,
                (
                    "reference_alignment_group_"
                    "output_count_sha256"
                ): PRIVATE_OUTPUT_SHA256,
                "record_equivalent": True,
                "reference_dictionary_equivalent": True,
                "read_group_dictionary_equivalent": True,
                "alignment_group_output_count_equivalent": True,
                "exact_oracle_match": True,
            },
        )
        write_json(
            oracle_root / "cross-implementation-receipt.json",
            cross_gate,
        )

        directional_receipt = (
            oracle_root / "directional-oracle-receipt.json"
        )
        eligible_records = 5 if self.paired else 10
        excluded_second_directional = 5 if self.paired else 0
        directional_metric_common = {
            "input_bytes": input_bytes,
            "records": eligible_records,
            "input_records": 10,
            "eligible_records": eligible_records,
            "excluded_unmapped": 0,
            "excluded_second_of_pair": excluded_second_directional,
            "excluded_unpaired": 0,
            "excluded_mate_unmapped": 0,
            "excluded_chimeric": 0,
            "alignment_groups": 5,
            "clusters": 5,
            "umi_memberships": 5,
            "max_umi_memberships_per_cluster": 1,
            "record_key_bytes": 100,
            "membership_partition_bytes": 100,
            "membership_partition_sha256": PRIVATE_SEMANTIC_SHA256,
            "rooted_partition_bytes": 120,
            "rooted_partition_sha256": PRIVATE_ALTERNATE_SEMANTIC_SHA256,
            "alignment_umi_frequency_multiset_bytes": 80,
            "alignment_umi_frequency_multiset_sha256": PRIVATE_BAI_SHA256,
            "reference_sequences": 2,
            "reference_dictionary_sha256": PRIVATE_REFERENCE_SHA256,
            "read_groups": 0,
            "read_group_dictionary_sha256": PRIVATE_OUTPUT_SHA256,
        }
        source_directional_metrics = {
            **directional_metric_common,
            "input_sha256": PRIVATE_BAM_SHA256,
        }
        upstream_directional_metrics = {
            **directional_metric_common,
            "input_sha256": PRIVATE_ALTERNATE_OUTPUT_SHA256,
        }
        dumi_directional_metrics = {
            **directional_metric_common,
            "input_sha256": PRIVATE_OUTPUT_SHA256,
        }
        directional_gate = {
            "directional_oracle_gate_pass": True,
            "dumi_off_oracle_partition_equivalent": True,
            "dumi_off_oracle_root_assignment_equivalent": True,
            "dumi_off_source_reference_dictionary_equivalent": True,
            "dumi_off_source_read_group_dictionary_equivalent": True,
        }
        directional_diagnostics = {
            "canonical_upstream_oracle_partition_equivalent": True,
            "canonical_upstream_oracle_root_assignment_equivalent": True,
            "canonical_upstream_dumi_off_partition_equivalent": True,
            "canonical_upstream_dumi_off_root_assignment_equivalent": True,
            "canonical_upstream_source_reference_dictionary_equivalent": True,
            "canonical_upstream_source_read_group_dictionary_equivalent": True,
        }
        directional_methods = dict(EXPORTER.DIRECTIONAL_ORACLE_METHODS)
        write_json(
            directional_receipt,
            {
                "schema": EXPORTER.DIRECTIONAL_ORACLE_SCHEMA,
                "version": EXPORTER.DIRECTIONAL_ORACLE_SCHEMA_VERSION,
                "methods": directional_methods,
                "source_oracle": source_directional_metrics,
                "canonical_upstream": upstream_directional_metrics,
                "dumi_off": dumi_directional_metrics,
                "gate": directional_gate,
                "diagnostics": directional_diagnostics,
                "provenance": {
                    "helper_sha256": sha256(
                        self.bundle
                        / "harness"
                        / "directional_oracle_check.py"
                    ),
                    "partition_checker_sha256": sha256(
                        self.bundle
                        / "harness"
                        / "cluster_partition_check.py"
                    ),
                    "private_streams_retained": False,
                },
                "configuration": {
                    "mode": pairing_mode,
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
                "temporary_storage": {
                    "persistent_stage_peak_upper_bound_bytes": 840,
                    "sort_merge_storage_note": (
                        "bounded external-sort merge files are additional "
                        "and scale linearly with the active stream"
                    ),
                },
            },
        )
        pairwise_receipt = (
            oracle_root
            / "pairwise-cluster-diagnostic-receipt.json"
        )
        pairwise_side = {
            "input_records": 10,
            "eligible_records": eligible_records,
            "excluded_unmapped": 0,
            "excluded_second_of_pair": excluded_second_directional,
            "excluded_unpaired": 0,
            "excluded_mate_unmapped": 0,
            "excluded_chimeric": 0,
            "alignment_groups": 5,
            "clusters": 5,
            "umi_memberships": 5,
            "max_umi_memberships_per_cluster": 1,
            "record_key_bytes": 100,
            "canonical_partition_bytes": 100,
            "partition_cluster_multiset_sha256": PRIVATE_SEMANTIC_SHA256,
            "reference_sequences": 2,
            "reference_dictionary_sha256": PRIVATE_REFERENCE_SHA256,
            "read_groups": 0,
            "read_group_dictionary_sha256": PRIVATE_OUTPUT_SHA256,
        }
        write_json(
            pairwise_receipt,
            {
                "schema": "dumi-cluster-partition-check-v1",
                "partition_fingerprint_version": (
                    "umicollapse-tag-alignment-cluster-umi-frequency-v1"
                ),
                "equivalent": True,
                "partition_equivalent": True,
                "reference_dictionary_equivalent": True,
                "read_group_dictionary_equivalent": True,
                "configuration": {
                    "mode": pairing_mode,
                    "umi_length": 12,
                    "umi_separator_bytes": 1,
                    "umi_separator_sha256": hashlib.sha256(b"_").hexdigest(),
                    "remove_unpaired": False,
                    "remove_chimeric": False,
                    "sort_buffer_size": "256M",
                },
                "left": pairwise_side,
                "right": dict(pairwise_side),
                "temporary_storage": {
                    "persistent_stage_peak_upper_bound_bytes": 300,
                    "sort_merge_storage_note": (
                        "bounded external-sort merge files are additional "
                        "and scale linearly with the active stage"
                    ),
                },
            },
        )
        directional_receipt_sha256 = sha256(directional_receipt)
        pairwise_receipt_sha256 = sha256(pairwise_receipt)
        environment = {
            "captured_at_utc": "2030-01-01T00:00:00+00:00",
            "platform": "Linux-test-x86_64",
            "python": "Python 3.12.0",
            "logical_cpu_count": 8,
            "load_average_1m_5m_15m": [0.1, 0.2, 0.3],
            "removed_injection_environment_variables": [],
            "environment_policy": "allowlist",
            "network_environment_variable_names": [],
            "subprocess_environment": {
                "HOME": "<EVIDENCE_DIR>/process-home",
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/opt/example/bin",
                "TMPDIR": "<EVIDENCE_DIR>/process-tmp",
                "TZ": "UTC",
            },
            "cpu_affinity": [0, 1, 2, 3],
            "cpu_scaling_governor": "performance",
            "uname": "Linux test x86_64",
            "java": "openjdk version 21.0.2\nruntime detail",
            "javac": "javac 21.0.2",
            "samtools": "samtools 1.20\nbuild detail",
            "gnu_sort": "sort (GNU coreutils) 9.4",
            "gnu_time": "time (GNU Time) 1.9",
            "git": "git version 2.45.0",
            "lscpu": "Architecture: x86_64",
        }
        jvm_options = [
            "-XX:-UsePerfData",
            "-server",
            "-Xms64m",
            "-Xmx2g",
            "-Xss20m",
            "-XX:ActiveProcessorCount=4",
        ]
        cluster_jvm_options = [
            "-XX:-UsePerfData",
            "-server",
            "-Xms64m",
            "-Xss20m",
            "-XX:ActiveProcessorCount=4",
            "-Xmx2g",
        ]
        runtime_id = hashlib.sha256(
            json.dumps(
                {
                    "java": environment["java"],
                    "javac": environment["javac"],
                    "dependencies": [
                        {
                            "filename": "htsjdk-3.0.5.jar",
                            "sha256": PUBLIC_DEPENDENCY_SHA256,
                        },
                        {
                            "filename": "snappy-java-1.1.10.8.jar",
                            "sha256": PUBLIC_SNAPPY_SHA256,
                        },
                    ],
                    "jvm_options": jvm_options,
                    "cluster_tag_jvm_options": cluster_jvm_options,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        provenance_ledger_receipt = {
            "schema": EXPORTER.EXTERNAL_PROVENANCE_LEDGER_SCHEMA,
            "version": EXPORTER.EXTERNAL_PROVENANCE_LEDGER_VERSION,
            "sha256": PRIVATE_PROVENANCE_LEDGER_SHA256,
            "workload_count": 1,
            "authorization_confirmed": True,
            "pre_deduplication_confirmed": True,
            "path_recorded": False,
            "content_retained": False,
        }
        write_json(
            self.bundle / "STATUS.json",
            {
                "detail": "",
                "state": "COMPLETE",
                "updated_at_utc": "2030-01-01T00:00:00+00:00",
            },
        )
        write_json(
            self.bundle / "manifest.json",
            {
                "format": 2,
                "timing_design_version": 2,
                "created_at_utc": "2030-01-01T00:00:00+00:00",
                "publication_profile": "restricted-method-auditable",
                "contains_source_content_hashes": True,
                "automatic_publication": False,
                "canonical": {
                    "url": (
                        "https://github.com/"
                        "Daniel-Liu-c0deb0t/UMICollapse.git"
                    ),
                    "provenance_ref": "refs/heads/master",
                    "sha": CANONICAL_SHA,
                },
                "intermediate": None,
                "dumi": {
                    "url": "https://github.com/justinblethrow-cloud/dUMI.git",
                    "ref": None,
                    "ref_recorded": False,
                    "sha": DUMI_SHA,
                    "uncommitted_worktree_sources_excluded": True,
                    "worktree_was_dirty": False,
                },
                "dependencies": [
                    {
                        "filename": "htsjdk-3.0.5.jar",
                        "sha256": PUBLIC_DEPENDENCY_SHA256,
                        "url": EXPORTER.PUBLIC_DEPENDENCY_URLS[
                            "htsjdk-3.0.5.jar"
                        ],
                    },
                    {
                        "filename": "snappy-java-1.1.10.8.jar",
                        "sha256": PUBLIC_SNAPPY_SHA256,
                        "url": EXPORTER.PUBLIC_DEPENDENCY_URLS[
                            "snappy-java-1.1.10.8.jar"
                        ],
                    },
                ],
                "dependency_files": [
                    {
                        "path": "dependencies/htsjdk-3.0.5.jar",
                        "sha256": PUBLIC_DEPENDENCY_SHA256,
                    },
                    {
                        "path": (
                            "dependencies/snappy-java-1.1.10.8.jar"
                        ),
                        "sha256": PUBLIC_SNAPPY_SHA256,
                    },
                ],
                "harness_commit_binding": {
                    "status": "verified",
                    "repository_url": (
                        "https://github.com/"
                        "justinblethrow-cloud/dUMI.git"
                    ),
                    "commit_sha": DUMI_SHA,
                    "files": [
                        {
                            "repository_path": repository_path,
                            "snapshot_path": snapshot_path,
                            "sha256": receipt["sha256"],
                        }
                        for repository_path, snapshot_path, receipt in zip(
                            EXPORTER.HARNESS_REPOSITORY_PATHS,
                            EXPORTER.HARNESS_PATHS,
                            harness_files,
                        )
                    ],
                },
                "harness_files": harness_files,
                "builds": builds,
                "config": {
                    "active_processors": 4,
                    "allow_output_in_repo": False,
                    "cluster_tag_xmx": "2g",
                    "cluster_tag_xmx_source": "explicit-cluster-tag-xmx",
                    "cluster_sort_command": "<GNU_SORT>",
                    "dumi_ref": None,
                    "dumi_source_sha": DUMI_SHA,
                    "external_workload_ids": [SOURCE_ID],
                    "hotspot_families": None,
                    "include_intermediate": False,
                    "input_mode": "external_bam",
                    "keep_outputs": False,
                    "moderate_families_per_group": None,
                    "moderate_groups": None,
                    "paired_pairs_per_reference": None,
                    "paired_references": [],
                    "profile": None,
                    "repetitions": 1,
                    "timing_design_version": 2,
                    "seed": None,
                    "selected_workloads": [],
                    "sparse_records": [],
                    "xms": "64m",
                    "xmx": "2g",
                },
                "external_inputs": [
                    {
                        "workload_id": SOURCE_ID,
                        "bytes": input_bytes,
                        "paired": self.paired,
                        "umi_length": 12,
                        "umi_separator": "_",
                        "rationale_provided": False,
                        "alias_neutrality_machine_verified": False,
                        "path_recorded": False,
                        "quickcheck_status": "pass",
                        "declared_sort_order": "coordinate",
                        "temporary_index_validation": "pass",
                        "sha256": PRIVATE_BAM_SHA256,
                        "paired_index": source_paired_index,
                        "reference_sequences": 2,
                        "reference_dictionary_sha256": (
                            PRIVATE_REFERENCE_SHA256
                        ),
                        "total_records": 10,
                        "mapped_records": 10,
                        "paired_records": 10 if self.paired else 0,
                        "qnames_checked": 5 if self.paired else 10,
                        "private_timing_snapshot": {
                            "kind": "verified_private_copy",
                            "bytes": input_bytes,
                            "sha256": PRIVATE_BAM_SHA256,
                            "read_only": True,
                            "path_recorded": False,
                            "paired_index": snapshot_paired_index,
                            "timing_index": {
                                "bytes": 100,
                                "sha256": PRIVATE_BAI_SHA256,
                                "format": "bai",
                                "path_recorded": False,
                            },
                            "retained_after_sealing": False,
                        },
                        "forced_on_contract": forced_on_contract,
                        "provenance_ledger": dict(
                            provenance_ledger_receipt
                        ),
                    }
                ],
                "external_provenance_ledger": dict(
                    provenance_ledger_receipt
                ),
                "subprocess_environment": {
                    "HOME": "<EVIDENCE_DIR>/process-home",
                    "LANG": "C",
                    "LC_ALL": "C",
                    "PATH": "/opt/example/bin",
                    "TMPDIR": "<EVIDENCE_DIR>/process-tmp",
                    "TZ": "UTC",
                },
                "jvm_options": jvm_options,
                "cluster_tag_jvm_options": cluster_jvm_options,
                "runtime_id": runtime_id,
                "implementation_sources": {
                    "canonical-upstream/legacy": CANONICAL_SHA,
                    "dumi/off": DUMI_SHA,
                    "dumi/on": DUMI_SHA,
                    "dumi/auto": DUMI_SHA,
                },
                "workloads": [
                    {
                        "name": "external",
                        "scale": SOURCE_ID,
                        "umi_length": 12,
                        "umi_separator": "_",
                        "paired": self.paired,
                        "input_mode": "external_bam",
                        "streaming_on_eligible": streaming_eligible,
                        "forced_on_contract_recorded": True,
                        "generator_arguments": [],
                        "rationale_provided": False,
                        "directional_oracle_gate": {
                            "applicable": True,
                            "status": "pass",
                            "input": "verified-private-timing-snapshot",
                            "input_sha256": PRIVATE_BAM_SHA256,
                            **directional_gate,
                            "diagnostics": directional_diagnostics,
                            "methods": directional_methods,
                            "tagged_outputs_retained": False,
                            "private_oracle_streams_retained": False,
                            "untimed": True,
                            "receipt": (
                                f"oracles/external/{SOURCE_ID}/"
                                "directional-oracle-receipt.json"
                            ),
                            "receipt_sha256": directional_receipt_sha256,
                            "post_timing_capacity_receipt": (
                                f"inputs/external/{SOURCE_ID}/"
                                "post-timing-oracle-scratch-capacity.json"
                            ),
                            "post_timing_capacity_required_available_bytes": (
                                post_required_bytes
                            ),
                            "post_timing_capacity_available_bytes": (
                                post_required_bytes + 1024
                            ),
                        },
                        "pairwise_cluster_diagnostic": {
                            "applicable": True,
                            "status": "match",
                            "scope": "diagnostic-only",
                            "equivalent": True,
                            "partition_equivalent": True,
                            "reference_dictionary_equivalent": True,
                            "read_group_dictionary_equivalent": True,
                            "tagged_outputs_retained": False,
                            "private_partition_streams_retained": False,
                            "untimed": True,
                            "receipt": (
                                f"oracles/external/{SOURCE_ID}/"
                                "pairwise-cluster-diagnostic-receipt.json"
                            ),
                            "receipt_sha256": pairwise_receipt_sha256,
                        },
                        "performance_comparability": {
                            "applicable": True,
                            "status": "comparable",
                            "issues": [],
                            "cross_implementation_exact_match": True,
                            "cross_implementation_output_count_match": True,
                            (
                                "cross_implementation_alignment_group_"
                                "output_count_match"
                            ): True,
                        },
                        "timing_stage_schedule": {
                            "timing_design_version": 2,
                            "scope": "per-workload",
                            "execution_order": [
                                "raw",
                                "end_to_end_ready",
                            ],
                            "treatments": treatment_count,
                            "repetitions": 1,
                            "order_family": (
                                "cyclic-latin-fallback-nonreportable"
                                if self.paired
                                else "williams-first-order-balanced"
                            ),
                            "complete_order_cycles": False,
                            "publication_grade_external_schedule": False,
                            "raw_cells": treatment_count,
                            "end_to_end_ready_cells": treatment_count,
                            "raw_order_offset": 0,
                            "end_to_end_ready_order": (
                                "independent-stage-offset"
                            ),
                            "end_to_end_ready_order_offset": 1,
                            "cross_stage_order_matching_required": False,
                            "fresh_deduplication_per_stage_cell": True,
                            "validation_and_deletion": (
                                "after-complete-repetition-block"
                            ),
                            "capacity_receipt": (
                                f"inputs/external/{SOURCE_ID}/"
                                "stage-scratch-capacity.json"
                            ),
                            "capacity_status": "pass",
                            "capacity_available_bytes": (
                                required_bytes + 1024
                            ),
                            "capacity_required_available_bytes": (
                                required_bytes
                            ),
                            "capacity_timing_peak_stage_bytes": (
                                timing_peak_bytes
                            ),
                            "capacity_directional_oracle_peak_stage_bytes": (
                                directional_peak_bytes
                            ),
                        },
                    }
                ],
            },
        )
        write_json(self.bundle / "environment.json", environment)
        (self.bundle / "environment.txt").write_text(
            "restricted environment detail\n", encoding="utf-8"
        )
        (self.bundle / "environment.txt").chmod(0o600)
        self._write_tables()
        write_json(
            self.bundle / "privacy-scan.json",
            {"rules": ["restricted pre-export scan"], "status": "pass"},
        )
        write_json(
            self.bundle / "external-log-redaction.json",
            {
                "policy": "restricted input-touching logs suppressed",
                "redacted_files": sorted(self.redacted_paths),
                "status": "pass",
            },
        )
        self.reseal()

    def _write_tables(self) -> None:
        design: list[dict[str, str]] = []
        measurements: list[dict[str, str]] = []
        summary: list[dict[str, str]] = []
        correctness: list[dict[str, str]] = []
        self.redacted_paths: list[str] = []
        treatment_keys = tuple(
            (treatment["implementation"], treatment["mode"])
            for treatment in self.treatments()
        )
        ready_orders = {
            treatment: str(order)
            for order, treatment in enumerate(
                EXPORTER.expected_balanced_order(treatment_keys, 1), 1
            )
        }
        for treatment in self.treatments():
            for stage in ("raw", "end_to_end_ready"):
                order = (
                    treatment["order"]
                    if stage == "raw"
                    else ready_orders[
                        (
                            treatment["implementation"],
                            treatment["mode"],
                        )
                    ]
                )
                label = (
                    treatment["implementation"]
                    if treatment["mode"] == "legacy"
                    else (
                        f"{treatment['implementation']}-"
                        f"{treatment['mode']}"
                    )
                )
                base_run_id = (
                    f"external-{SOURCE_ID}-r01-o{int(order):02d}-{label}"
                )
                run_id = f"{base_run_id}-{stage}"
                run_root = self.bundle / "runs" / base_run_id / stage
                run_root.mkdir(parents=True)
                raw_output = (
                    run_root / "output.bam"
                    if stage == "raw"
                    else (
                        run_root / "intermediate.raw.private.bam"
                        if treatment["route"] == "streaming"
                        else run_root / "output.coordinate.bam"
                    )
                )
                measured_output = (
                    run_root / "output.bam"
                    if stage == "raw"
                    else run_root / "output.coordinate.bam"
                )
                source_key = (
                    "upstream"
                    if treatment["implementation"]
                    == "canonical-upstream"
                    else "dumi"
                )
                command_tokens = [
                    "<JAVA>",
                    "-XX:-UsePerfData",
                    "-server",
                    "-Xms64m",
                    "-Xmx2g",
                    "-Xss20m",
                    "-XX:ActiveProcessorCount=4",
                    (
                        "-Djava.io.tmpdir=<EVIDENCE_DIR>/"
                        f"runs/{base_run_id}/{stage}/java-tmp"
                    ),
                    "-cp",
                    (
                        f"<EVIDENCE_DIR>/classes/{source_key}:"
                        "<EVIDENCE_DIR>/dependencies/htsjdk-3.0.5.jar:"
                        "<EVIDENCE_DIR>/dependencies/"
                        "snappy-java-1.1.10.8.jar"
                    ),
                    "umicollapse.main.Main",
                    "bam",
                    "-i",
                    (
                        f"<EVIDENCE_DIR>/private-inputs/{SOURCE_ID}/"
                        "input.private.bam"
                    ),
                    "-o",
                    (
                        f"<EVIDENCE_DIR>/runs/{base_run_id}/{stage}/"
                        f"{raw_output.name}"
                    ),
                    "-u",
                    "12",
                    "--algo",
                    "dir",
                    "-k",
                    "1",
                    "-p",
                    ".5",
                    "--data",
                    "ngrambktree",
                    "--merge",
                    "mapqual",
                    "--umi-sep",
                    (
                        r"\Q_\E"
                        if treatment["implementation"]
                        == "canonical-upstream"
                        else "_"
                    ),
                ]
                if treatment["implementation"] == "dumi":
                    command_tokens.extend(
                        ["--streaming-mode", treatment["mode"]]
                    )
                if self.paired:
                    command_tokens.insert(
                        command_tokens.index("--streaming-mode")
                        if "--streaming-mode" in command_tokens
                        else len(command_tokens),
                        "--paired",
                    )
                if stage == "raw":
                    command_text = EXPORTER.runner_sanitized_shlex_join(
                        command_tokens
                    )
                else:
                    command_shell = (
                        EXPORTER.runner_sanitized_shlex_join(
                            command_tokens
                        )
                    )
                    final_path = (
                        f"<EVIDENCE_DIR>/runs/{base_run_id}/{stage}/"
                        "output.coordinate.bam"
                    )
                    if treatment["route"] == "streaming":
                        command_shell += (
                            f" && <SAMTOOLS> sort -o {final_path} "
                            f"<EVIDENCE_DIR>/runs/{base_run_id}/{stage}/"
                            "intermediate.raw.private.bam"
                        )
                    command_shell += f" && <SAMTOOLS> index {final_path}"
                    command_text = EXPORTER.shlex.join(
                        ["bash", "-c", command_shell]
                    )
                (run_root / "command.txt").write_text(
                    command_text + "\n", encoding="utf-8"
                )
                (run_root / "time.tsv").write_text(
                    (
                        f"{treatment['elapsed']}\t0.8\t0.1\t90%\t"
                        f"{treatment['rss']}\t0\n"
                    ),
                    encoding="utf-8",
                )
                (run_root / "monotonic-wall-seconds.txt").write_text(
                    f"{treatment['elapsed']}\n", encoding="utf-8"
                )
                for log_name in ("stdout.txt", "stderr.txt"):
                    log_path = run_root / log_name
                    log_path.write_text(
                        EXPORTER.REDACTED_LOG_CONTENT, encoding="utf-8"
                    )
                    self.redacted_paths.append(
                        log_path.relative_to(self.bundle).as_posix()
                    )
                oracle_label = (
                    "canonical-upstream"
                    if treatment["implementation"]
                    == "canonical-upstream"
                    else "dumi-off"
                )
                sort_order = (
                    "unsorted"
                    if stage == "raw"
                    and treatment["route"] == "streaming"
                    else "coordinate"
                )
                write_json(
                    run_root / "inspection.json",
                    {
                        "output_file": (
                            measured_output.relative_to(self.bundle).as_posix()
                        ),
                        "quickcheck": True,
                        "quickcheck_status": "pass",
                        "output_records": 1000,
                        "semantic_sha256": PRIVATE_SEMANTIC_SHA256,
                        "sort_order": sort_order,
                        "reference_sequences": 2,
                        "reference_dictionary_sha256": (
                            PRIVATE_REFERENCE_SHA256
                        ),
                        "read_groups": 0,
                        "read_group_dictionary_sha256": (
                            PRIVATE_OUTPUT_SHA256
                        ),
                        "expected_reference_sequences": 2,
                        "expected_reference_dictionary_sha256": (
                            PRIVATE_REFERENCE_SHA256
                        ),
                        "expected_read_groups": 0,
                        "expected_read_group_dictionary_sha256": (
                            PRIVATE_OUTPUT_SHA256
                        ),
                        "alignment_group_fingerprint_version": (
                            EXPORTER.ALIGNMENT_GROUP_FINGERPRINT_VERSION
                        ),
                        "alignment_group_mode": (
                            "paired" if self.paired else "single-end"
                        ),
                        "alignment_group_output_records": (
                            500 if self.paired else 1000
                        ),
                        "alignment_group_records_excluded_unmapped": 0,
                        (
                            "alignment_group_records_"
                            "excluded_second_of_pair"
                        ): 500 if self.paired else 0,
                        "alignment_group_output_count_sha256": (
                            PRIVATE_OUTPUT_SHA256
                        ),
                        (
                            "alignment_group_output_count_"
                            "reused_from_exact_reference"
                        ): True,
                        "reference_file": (
                            f"<EVIDENCE_DIR>/oracles/external/{SOURCE_ID}/"
                            f"{oracle_label}/output.private.bam"
                        ),
                        "reference_file_sha256": PRIVATE_OUTPUT_SHA256,
                        "reference_canonical_sha256": (
                            PRIVATE_SEMANTIC_SHA256
                        ),
                        "reference_canonical_sha256_verified": True,
                        "reference_cache_receipt_verified": True,
                        "reference_cache_receipt_sha256": PRIVATE_BAI_SHA256,
                        "reference_alignment_group_output_records": (
                            500 if self.paired else 1000
                        ),
                        (
                            "reference_alignment_group_records_"
                            "excluded_unmapped"
                        ): 0,
                        (
                            "reference_alignment_group_records_"
                            "excluded_second_of_pair"
                        ): 500 if self.paired else 0,
                        (
                            "reference_alignment_group_"
                            "output_count_sha256"
                        ): PRIVATE_OUTPUT_SHA256,
                        "record_equivalent": True,
                        "reference_dictionary_equivalent": True,
                        "read_group_dictionary_equivalent": True,
                        "alignment_group_output_count_equivalent": True,
                        "output_bytes": 50000,
                        "output_sha256": PRIVATE_OUTPUT_SHA256,
                        "exact_oracle_match": True,
                        "actual_route": treatment["route"],
                    },
                )
                for path in run_root.iterdir():
                    path.chmod(0o600)
                design_row = {
                    "run_id": run_id,
                    "workload": "external",
                    "scale": SOURCE_ID,
                    "stage": stage,
                    "implementation": treatment["implementation"],
                    "mode": treatment["mode"],
                    "repetition": "1",
                    "order": order,
                }
                design.append(design_row)
                measurement = {
                    field: "" for field in EXPORTER.MEASUREMENT_INPUT_FIELDS
                }
                measurement.update(
                    {
                        **design_row,
                        "exit_code": "0",
                        "elapsed_s": treatment["elapsed"],
                        "user_s": "0.8",
                        "system_s": "0.1",
                        "cpu_pct": "90%",
                        "max_rss_kib": treatment["rss"],
                        "input_sha256": PRIVATE_BAM_SHA256,
                        "output_records": "1000",
                        "semantic_sha256": PRIVATE_SEMANTIC_SHA256,
                        "sort_order": sort_order,
                        "output_bytes": "50000",
                        "output_sha256": PRIVATE_OUTPUT_SHA256,
                        "reference_sequences": "2",
                        "reference_dictionary_sha256": PRIVATE_REFERENCE_SHA256,
                        "expected_output_records": "1000",
                        "expected_semantic_sha256": PRIVATE_SEMANTIC_SHA256,
                        "expected_reference_sequences": "2",
                        "expected_reference_dictionary_sha256": (
                            PRIVATE_REFERENCE_SHA256
                        ),
                        "actual_route": treatment["route"],
                        "oracle_implementation": (
                            "canonical-upstream"
                            if treatment["implementation"] == "canonical-upstream"
                            else "dumi-off"
                        ),
                        "exact_oracle_match": "True",
                        "cross_implementation_exact_match": "True",
                        "cross_implementation_output_count_match": "True",
                        (
                            "cross_implementation_alignment_group_output_count_match"
                        ): "True",
                        "directional_oracle_gate_pass": "True",
                        "dumi_off_oracle_partition_equivalent": "True",
                        (
                            "dumi_off_oracle_root_assignment_equivalent"
                        ): "True",
                        (
                            "canonical_upstream_oracle_partition_equivalent"
                        ): "True",
                        (
                            "canonical_upstream_oracle_"
                            "root_assignment_equivalent"
                        ): "True",
                        (
                            "canonical_upstream_dumi_off_"
                            "partition_equivalent"
                        ): "True",
                        (
                            "canonical_upstream_dumi_off_"
                            "root_assignment_equivalent"
                        ): "True",
                        "directional_oracle_receipt": (
                            f"oracles/external/{SOURCE_ID}/"
                            "directional-oracle-receipt.json"
                        ),
                        "command_file": (
                            f"runs/{base_run_id}/{stage}/command.txt"
                        ),
                        "stdout_file": (
                            f"runs/{base_run_id}/{stage}/stdout.txt"
                        ),
                        "stderr_file": (
                            f"runs/{base_run_id}/{stage}/stderr.txt"
                        ),
                    }
                )
                measurements.append(measurement)

                summary_row = {
                    field: "" for field in EXPORTER.SUMMARY_INPUT_FIELDS
                }
                summary_row.update(
                    {
                        "workload": "external",
                        "scale": SOURCE_ID,
                        "stage": stage,
                        "implementation": treatment["implementation"],
                        "mode": treatment["mode"],
                        "attempts": "1",
                        "successful_repetitions": "1",
                        "failed_repetitions": "0",
                        "correctness_status": "pass",
                        "comparability_status": "comparable",
                        "comparability_issues": "",
                        "input_sha256": PRIVATE_BAM_SHA256,
                        "output_records": "1000",
                        "semantic_sha256": PRIVATE_SEMANTIC_SHA256,
                        "sort_order": sort_order,
                        "reference_sequences": "2",
                        "reference_dictionary_sha256": (
                            PRIVATE_REFERENCE_SHA256
                        ),
                    }
                )
                for metric, value in (
                    ("elapsed_s", treatment["elapsed"]),
                    ("user_s", "0.8"),
                    ("system_s", "0.1"),
                    ("cpu_pct", "90"),
                    ("max_rss_kib", treatment["rss"]),
                ):
                    summary_row[f"{metric}_n"] = "1"
                    summary_row[f"{metric}_median"] = value
                    summary_row[f"{metric}_min"] = value
                    summary_row[f"{metric}_max"] = value
                    summary_row[f"{metric}_range"] = "0"
                    summary_row[f"{metric}_mad"] = "0"
                summary.append(summary_row)
                correctness.append(
                    {
                        **{
                            field: ""
                            for field in EXPORTER.CORRECTNESS_INPUT_FIELDS
                        },
                        **{
                            "workload": "external",
                            "scale": SOURCE_ID,
                            "stage": stage,
                            "implementation": treatment["implementation"],
                            "mode": treatment["mode"],
                            "correctness_status": "pass",
                            "directional_oracle_gate_pass": "True",
                            "dumi_off_oracle_partition_equivalent": "True",
                            (
                                "dumi_off_oracle_"
                                "root_assignment_equivalent"
                            ): "True",
                            (
                                "canonical_upstream_oracle_"
                                "partition_equivalent"
                            ): "True",
                            (
                                "canonical_upstream_oracle_"
                                "root_assignment_equivalent"
                            ): "True",
                            (
                                "canonical_upstream_dumi_off_"
                                "partition_equivalent"
                            ): "True",
                            (
                                "canonical_upstream_dumi_off_"
                                "root_assignment_equivalent"
                            ): "True",
                            "directional_oracle_receipt": (
                                f"oracles/external/{SOURCE_ID}/"
                                "directional-oracle-receipt.json"
                            ),
                            "issue_count": "0",
                            "issues": "",
                        },
                    }
                )

        comparisons: list[dict[str, str]] = []
        for treatment in self.treatments()[1:]:
            for stage in ("raw", "end_to_end_ready"):
                row = {
                    field: "" for field in EXPORTER.COMPARISON_INPUT_FIELDS
                }
                row.update(
                    {
                        "workload": "external",
                        "scale": SOURCE_ID,
                        "stage": stage,
                        "baseline_implementation": "canonical-upstream",
                        "baseline_mode": "legacy",
                        "implementation": treatment["implementation"],
                        "mode": treatment["mode"],
                        "attempted_pairs": "1",
                        "successful_pairs": "1",
                        "failed_pairs": "0",
                        "noncomparable_pairs": "0",
                        "correctness_status": "pass",
                        "issues": "",
                        "comparability_status": "comparable",
                        "comparability_issues": "",
                    }
                )
                for metric, value in (
                    (
                        "elapsed_speedup",
                        EXPORTER.format_decimal(
                            EXPORTER.Decimal("2.0")
                            / EXPORTER.Decimal(treatment["elapsed"])
                        ),
                    ),
                    (
                        "elapsed_change_pct",
                        EXPORTER.format_decimal(
                            (
                                EXPORTER.Decimal(treatment["elapsed"])
                                - EXPORTER.Decimal("2.0")
                            )
                            * EXPORTER.Decimal(100)
                            / EXPORTER.Decimal("2.0")
                        ),
                    ),
                    (
                        "max_rss_reduction_pct",
                        EXPORTER.format_decimal(
                            (
                                EXPORTER.Decimal("100000")
                                - EXPORTER.Decimal(treatment["rss"])
                            )
                            * EXPORTER.Decimal(100)
                            / EXPORTER.Decimal("100000")
                        ),
                    ),
                ):
                    row[f"{metric}_n"] = "1"
                    row[f"{metric}_median"] = value
                    row[f"{metric}_min"] = value
                    row[f"{metric}_max"] = value
                    row[f"{metric}_range"] = "0"
                    row[f"{metric}_mad"] = "0"
                comparisons.append(row)

        write_tsv(
            self.bundle / "design.tsv", EXPORTER.DESIGN_INPUT_FIELDS, design
        )
        write_tsv(
            self.bundle / "measurements.tsv",
            EXPORTER.MEASUREMENT_INPUT_FIELDS,
            measurements,
        )
        write_tsv(
            self.bundle / "summary.tsv",
            EXPORTER.SUMMARY_INPUT_FIELDS,
            summary,
        )
        write_tsv(
            self.bundle / "comparisons.tsv",
            EXPORTER.COMPARISON_INPUT_FIELDS,
            comparisons,
        )
        write_tsv(
            self.bundle / "correctness.tsv",
            EXPORTER.CORRECTNESS_INPUT_FIELDS,
            correctness,
        )

    def write_aliases(self, aliases: dict[str, str] | None = None) -> None:
        write_json(
            self.alias_map,
            {
                "format": 1,
                "aliases": (
                    {
                        SOURCE_ID: (
                            "panel-pe-01" if self.paired else PUBLIC_ALIAS
                        )
                    }
                    if aliases is None
                    else aliases
                ),
            },
        )

    def write_denylist(
        self,
        *,
        tokens: list[str] | None = None,
        paths: list[str] | None = None,
        hashes: list[str] | None = None,
    ) -> None:
        write_json(
            self.denylist,
            {
                "format": 1,
                "tokens": [PRIVATE_TOKEN, PRIVATE_BRAND]
                if tokens is None
                else tokens,
                "paths": [PRIVATE_PATH] if paths is None else paths,
                "hashes": [PRIVATE_BAM_SHA256, PRIVATE_BAI_SHA256]
                if hashes is None
                else hashes,
            },
        )

    def make_cross_implementation_inexact(self) -> None:
        input_hash_path = (
            self.bundle
            / "inputs"
            / "external"
            / SOURCE_ID
            / "hashes.json"
        )
        hashes = json.loads(input_hash_path.read_text(encoding="utf-8"))
        hashes["oracles"]["canonical_upstream"]["semantic_sha256"] = (
            PRIVATE_ALTERNATE_SEMANTIC_SHA256
        )
        hashes["cross_implementation_diagnostic"]["exact_match"] = False
        write_json(input_hash_path, hashes)

        oracle_root = (
            self.bundle / "oracles" / "external" / SOURCE_ID
        )
        cross_path = oracle_root / "cross-implementation-receipt.json"
        cross = json.loads(cross_path.read_text(encoding="utf-8"))
        cross["exact_match"] = False
        write_json(cross_path, cross)
        canonical_path = (
            oracle_root / "canonical-upstream" / "inspection.json"
        )
        canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
        canonical["semantic_sha256"] = PRIVATE_ALTERNATE_SEMANTIC_SHA256
        canonical["output_sha256"] = PRIVATE_ALTERNATE_OUTPUT_SHA256
        canonical["record_equivalent"] = False
        canonical["exact_oracle_match"] = False
        canonical[
            "alignment_group_output_count_reused_from_exact_reference"
        ] = False
        write_json(canonical_path, canonical)

        measurements_path = self.bundle / "measurements.tsv"
        measurements = read_tsv(measurements_path)
        for row in measurements:
            row["cross_implementation_exact_match"] = "False"
            if row["implementation"] == "canonical-upstream":
                row["semantic_sha256"] = (
                    PRIVATE_ALTERNATE_SEMANTIC_SHA256
                )
                row["expected_semantic_sha256"] = (
                    PRIVATE_ALTERNATE_SEMANTIC_SHA256
                )
                row["output_sha256"] = PRIVATE_ALTERNATE_OUTPUT_SHA256
                inspection_path = (
                    self.bundle
                    / "runs"
                    / row["run_id"].removesuffix(
                        f"-{row['stage']}"
                    )
                    / row["stage"]
                    / "inspection.json"
                )
                inspection = json.loads(
                    inspection_path.read_text(encoding="utf-8")
                )
                inspection["semantic_sha256"] = (
                    PRIVATE_ALTERNATE_SEMANTIC_SHA256
                )
                inspection["output_sha256"] = (
                    PRIVATE_ALTERNATE_OUTPUT_SHA256
                )
                inspection["reference_file_sha256"] = (
                    PRIVATE_ALTERNATE_OUTPUT_SHA256
                )
                inspection["reference_canonical_sha256"] = (
                    PRIVATE_ALTERNATE_SEMANTIC_SHA256
                )
                write_json(inspection_path, inspection)
        write_tsv(
            measurements_path,
            EXPORTER.MEASUREMENT_INPUT_FIELDS,
            measurements,
        )
        summary_path = self.bundle / "summary.tsv"
        summary = read_tsv(summary_path)
        for row in summary:
            if row["implementation"] == "canonical-upstream":
                row["semantic_sha256"] = (
                    PRIVATE_ALTERNATE_SEMANTIC_SHA256
                )
        write_tsv(summary_path, EXPORTER.SUMMARY_INPUT_FIELDS, summary)
        manifest_path = self.bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["workloads"][0]["performance_comparability"][
            "cross_implementation_exact_match"
        ] = False
        write_json(manifest_path, manifest)
        self.reseal()

    def make_cross_output_count_mismatch(self) -> None:
        canonical_records = 999
        alignment_records = (500 if self.paired else 1000) - 1
        input_hash_path = (
            self.bundle
            / "inputs"
            / "external"
            / SOURCE_ID
            / "hashes.json"
        )
        hashes = json.loads(input_hash_path.read_text(encoding="utf-8"))
        canonical_identity = hashes["oracles"]["canonical_upstream"]
        canonical_identity["output_records"] = canonical_records
        canonical_identity["semantic_sha256"] = (
            PRIVATE_ALTERNATE_SEMANTIC_SHA256
        )
        cross = hashes["cross_implementation_diagnostic"]
        for field in (
            "exact_match",
            "output_count_match",
            "record_counts_equal",
            "alignment_group_output_record_counts_equal",
            "alignment_group_output_count_match",
            "alignment_group_output_count_multiset_equal",
        ):
            cross[field] = False
        cross["status"] = "difference"
        write_json(input_hash_path, hashes)

        oracle_root = self.bundle / "oracles" / "external" / SOURCE_ID
        write_json(
            oracle_root / "cross-implementation-receipt.json",
            cross,
        )
        canonical_path = (
            oracle_root / "canonical-upstream" / "inspection.json"
        )
        canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
        canonical.update(
            {
                "output_records": canonical_records,
                "semantic_sha256": PRIVATE_ALTERNATE_SEMANTIC_SHA256,
                "output_sha256": PRIVATE_ALTERNATE_OUTPUT_SHA256,
                "alignment_group_output_records": alignment_records,
                "alignment_group_output_count_sha256": (
                    PRIVATE_ALTERNATE_OUTPUT_SHA256
                ),
                "record_equivalent": False,
                "exact_oracle_match": False,
                "alignment_group_output_count_equivalent": False,
                (
                    "alignment_group_output_count_"
                    "reused_from_exact_reference"
                ): False,
            }
        )
        write_json(canonical_path, canonical)

        measurements_path = self.bundle / "measurements.tsv"
        measurements = read_tsv(measurements_path)
        for row in measurements:
            row["cross_implementation_exact_match"] = "False"
            row["cross_implementation_output_count_match"] = "False"
            row[
                "cross_implementation_alignment_group_output_count_match"
            ] = "False"
            if row["implementation"] != "canonical-upstream":
                continue
            row["output_records"] = str(canonical_records)
            row["expected_output_records"] = str(canonical_records)
            row["semantic_sha256"] = PRIVATE_ALTERNATE_SEMANTIC_SHA256
            row["expected_semantic_sha256"] = (
                PRIVATE_ALTERNATE_SEMANTIC_SHA256
            )
            row["output_sha256"] = PRIVATE_ALTERNATE_OUTPUT_SHA256
            inspection_path = (
                self.bundle
                / "runs"
                / row["run_id"].removesuffix(f"-{row['stage']}")
                / row["stage"]
                / "inspection.json"
            )
            inspection = json.loads(
                inspection_path.read_text(encoding="utf-8")
            )
            inspection.update(
                {
                    "output_records": canonical_records,
                    "semantic_sha256": PRIVATE_ALTERNATE_SEMANTIC_SHA256,
                    "output_sha256": PRIVATE_ALTERNATE_OUTPUT_SHA256,
                    "alignment_group_output_records": alignment_records,
                    "alignment_group_output_count_sha256": (
                        PRIVATE_ALTERNATE_OUTPUT_SHA256
                    ),
                    "reference_file_sha256": (
                        PRIVATE_ALTERNATE_OUTPUT_SHA256
                    ),
                    "reference_canonical_sha256": (
                        PRIVATE_ALTERNATE_SEMANTIC_SHA256
                    ),
                    "reference_alignment_group_output_records": (
                        alignment_records
                    ),
                    "reference_alignment_group_output_count_sha256": (
                        PRIVATE_ALTERNATE_OUTPUT_SHA256
                    ),
                }
            )
            write_json(inspection_path, inspection)
        write_tsv(
            measurements_path,
            EXPORTER.MEASUREMENT_INPUT_FIELDS,
            measurements,
        )

        summary_path = self.bundle / "summary.tsv"
        summary = read_tsv(summary_path)
        for row in summary:
            row["comparability_status"] = "not_comparable"
            row["comparability_issues"] = (
                EXPORTER.NONCOMPARABLE_OUTPUT_COUNT_ISSUE
            )
            if row["implementation"] == "canonical-upstream":
                row["output_records"] = str(canonical_records)
                row["semantic_sha256"] = (
                    PRIVATE_ALTERNATE_SEMANTIC_SHA256
                )
        write_tsv(summary_path, EXPORTER.SUMMARY_INPUT_FIELDS, summary)

        comparisons_path = self.bundle / "comparisons.tsv"
        comparisons = read_tsv(comparisons_path)
        for row in comparisons:
            row["successful_pairs"] = "0"
            row["noncomparable_pairs"] = row["attempted_pairs"]
            row["comparability_status"] = "not_comparable"
            row["comparability_issues"] = (
                EXPORTER.NONCOMPARABLE_OUTPUT_COUNT_ISSUE
            )
            for metric in EXPORTER.COMPARISON_METRICS:
                row[f"{metric}_n"] = "0"
                for statistic in ("median", "min", "max", "range", "mad"):
                    row[f"{metric}_{statistic}"] = ""
        write_tsv(
            comparisons_path,
            EXPORTER.COMPARISON_INPUT_FIELDS,
            comparisons,
        )

        manifest_path = self.bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        comparability = manifest["workloads"][0][
            "performance_comparability"
        ]
        comparability.update(
            {
                "status": "not_comparable",
                "issues": [EXPORTER.NONCOMPARABLE_OUTPUT_COUNT_ISSUE],
                "cross_implementation_exact_match": False,
                "cross_implementation_output_count_match": False,
                (
                    "cross_implementation_alignment_group_"
                    "output_count_match"
                ): False,
            }
        )
        write_json(manifest_path, manifest)
        self.reseal()

    def make_upstream_pairwise_diagnostic_difference(self) -> None:
        oracle_root = self.bundle / "oracles" / "external" / SOURCE_ID
        directional_path = oracle_root / "directional-oracle-receipt.json"
        directional = json.loads(
            directional_path.read_text(encoding="utf-8")
        )
        directional["canonical_upstream"][
            "membership_partition_sha256"
        ] = PRIVATE_ALTERNATE_OUTPUT_SHA256
        for field in (
            "canonical_upstream_oracle_partition_equivalent",
            "canonical_upstream_dumi_off_partition_equivalent",
        ):
            directional["diagnostics"][field] = False
        write_json(directional_path, directional)

        pairwise_path = (
            oracle_root / "pairwise-cluster-diagnostic-receipt.json"
        )
        pairwise = json.loads(pairwise_path.read_text(encoding="utf-8"))
        pairwise["left"]["partition_cluster_multiset_sha256"] = (
            PRIVATE_ALTERNATE_OUTPUT_SHA256
        )
        pairwise["partition_equivalent"] = False
        pairwise["equivalent"] = False
        write_json(pairwise_path, pairwise)

        manifest_path = self.bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        directional_manifest = manifest["workloads"][0][
            "directional_oracle_gate"
        ]
        directional_manifest["diagnostics"] = directional["diagnostics"]
        directional_manifest["receipt_sha256"] = sha256(directional_path)
        pairwise_manifest = manifest["workloads"][0][
            "pairwise_cluster_diagnostic"
        ]
        pairwise_manifest.update(
            {
                "status": "difference",
                "equivalent": False,
                "partition_equivalent": False,
                "receipt_sha256": sha256(pairwise_path),
            }
        )
        write_json(manifest_path, manifest)

        for filename, fields in (
            ("measurements.tsv", EXPORTER.MEASUREMENT_INPUT_FIELDS),
            ("correctness.tsv", EXPORTER.CORRECTNESS_INPUT_FIELDS),
        ):
            path = self.bundle / filename
            rows = read_tsv(path)
            for row in rows:
                row[
                    "canonical_upstream_oracle_partition_equivalent"
                ] = "False"
                row[
                    "canonical_upstream_dumi_off_partition_equivalent"
                ] = "False"
            write_tsv(path, fields, rows)
        self.reseal()

    def reseal(self) -> None:
        evidence_lines = [
            f"{sha256(self.bundle / filename)}  {filename}\n"
            for filename in EXPORTER.EVIDENCE_FILES
        ]
        (self.bundle / "evidence.sha256").write_text(
            "".join(evidence_lines), encoding="utf-8"
        )
        (self.bundle / "evidence.sha256").chmod(0o600)
        entries = sorted(
            path
            for path in self.bundle.rglob("*")
            if path.is_file()
            and path.name not in {"MANIFEST.sha256", "STATUS.json"}
        )
        (self.bundle / "MANIFEST.sha256").write_text(
            "".join(
                f"{sha256(path)}  {path.relative_to(self.bundle).as_posix()}\n"
                for path in entries
            ),
            encoding="utf-8",
        )
        (self.bundle / "MANIFEST.sha256").chmod(0o600)

    def command(
        self,
        *,
        description: str = "Neutral single-end demonstration panel",
        include_denylist: bool = True,
    ) -> list[str]:
        command = [
            sys.executable,
            str(EXPORTER_PATH),
            "--bundle",
            str(self.bundle),
            "--output-dir",
            str(self.output),
            "--alias-map",
            str(self.alias_map),
            "--panel-description",
            description,
            "--private-export-receipt",
            str(self.private_receipt),
            "--evidence-set-id",
            EVIDENCE_SET_ID,
        ]
        if include_denylist:
            command.extend(["--private-denylist", str(self.denylist)])
        return command

    def run(
        self,
        *,
        description: str = "Neutral single-end demonstration panel",
        include_denylist: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = self.command(
            description=description,
            include_denylist=include_denylist,
        )
        exporter_digest = sha256(EXPORTER_PATH)
        identity = {
            "repository_url": EXPORTER.DUMI_PUBLIC_URL,
            "repository_path": (
                "scripts/benchmark/export_public_external.py"
            ),
            "commit_sha": DUMI_SHA,
            "state": "clean",
            "tracked": True,
            "commit_blob_sha256": exporter_digest,
            "matches_commit": True,
        }
        error_stream = io.StringIO()
        output_stream = io.StringIO()
        with (
            mock.patch.object(
                EXPORTER,
                "exporter_git_identity",
                return_value=identity,
            ),
            redirect_stderr(error_stream),
            redirect_stdout(output_stream),
        ):
            try:
                returncode = EXPORTER.main(command[2:])
            except SystemExit as error:
                returncode = int(error.code or 0)
        return subprocess.CompletedProcess(
            command,
            returncode,
            stdout=output_stream.getvalue(),
            stderr=error_stream.getvalue(),
        )


class PublicExternalExportContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = RestrictedBundleFixture(self.root)
        exporter_digest = sha256(EXPORTER_PATH)
        self.identity_patcher = mock.patch.object(
            EXPORTER,
            "exporter_git_identity",
            return_value={
                "repository_url": EXPORTER.DUMI_PUBLIC_URL,
                "repository_path": (
                    "scripts/benchmark/export_public_external.py"
                ),
                "commit_sha": DUMI_SHA,
                "state": "clean",
                "tracked": True,
                "commit_blob_sha256": exporter_digest,
                "matches_commit": True,
            },
        )
        self.identity_patcher.start()
        self.addCleanup(self.identity_patcher.stop)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def public_bytes(self) -> bytes:
        return b"\n".join(
            path.read_bytes()
            for path in sorted(self.fixture.output.iterdir())
            if path.is_file()
        )

    def test_safe_export_is_strict_hash_free_and_verifiable(self) -> None:
        completed = self.fixture.run()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            {path.name for path in self.fixture.output.iterdir()},
            set(EXPORTER.PUBLIC_FILES),
        )
        self.assertEqual(
            os.stat(self.fixture.output).st_mode & 0o777,
            0o700,
        )
        self.assertTrue(
            all(
                (os.stat(path).st_mode & 0o777) == 0o600
                for path in self.fixture.output.iterdir()
            )
        )

        payload = self.public_bytes()
        for excluded in (
            SOURCE_ID,
            PRIVATE_TOKEN,
            PRIVATE_BRAND,
            PRIVATE_PATH,
            PRIVATE_BAM_SHA256,
            PRIVATE_BAI_SHA256,
            PRIVATE_SEMANTIC_SHA256,
            PRIVATE_OUTPUT_SHA256,
            PRIVATE_REFERENCE_SHA256,
            PRIVATE_PROVENANCE_LEDGER_SHA256,
            b"input_sha256".decode(),
            b"semantic_sha256".decode(),
            b"output_sha256".decode(),
            b"reference_dictionary_sha256".decode(),
            b"directional_oracle_receipt".decode(),
            b"external_provenance_ledger".decode(),
            b"command_file".decode(),
            b"PATH".decode(),
            b"HOME".decode(),
            b"TMPDIR".decode(),
        ):
            self.assertNotIn(str(excluded).encode(), payload)
        self.assertIn(PUBLIC_ALIAS.encode(), payload)
        self.assertIn(PUBLIC_DEPENDENCY_SHA256.encode(), payload)
        self.assertIn(CANONICAL_SHA.encode(), payload)
        self.assertIn(DUMI_SHA.encode(), payload)

        manifest = json.loads(
            (self.fixture.output / "manifest.public.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["restricted_bundle"]["status"], "verified-complete")
        self.assertEqual(manifest["evidence_set_id"], EVIDENCE_SET_ID)
        self.assertEqual(manifest["privacy"]["private_denylist_review"], "applied")
        self.assertFalse(
            manifest["privacy"]["private_data_derived_hashes_included"]
        )
        self.assertEqual(manifest["panel"]["workload_count"], 1)
        self.assertEqual(
            manifest["provenance_attestation"],
            {
                "schema": EXPORTER.EXTERNAL_PROVENANCE_LEDGER_SCHEMA,
                "version": EXPORTER.EXTERNAL_PROVENANCE_LEDGER_VERSION,
                "authorization_confirmed": True,
                "pre_deduplication_confirmed": True,
                "path_recorded": False,
                "content_retained": False,
            },
        )
        self.assertNotIn("sha256", manifest["provenance_attestation"])
        self.assertNotIn(
            "workload_count", manifest["provenance_attestation"]
        )
        self.assertEqual(
            manifest["build_provenance"],
            {
                "source_trees_commit_bound": True,
                "dependency_lock_bound": True,
                "build_commands_exactly_validated": True,
                "compiled_class_trees": (
                    "runner-attested-hashes-not-independently-rebuilt-by-exporter"
                ),
            },
        )
        public_readme = (
            self.fixture.output / "README.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "Compiled class-tree hashes remain runner-attested",
            " ".join(public_readme.split()),
        )
        self.assertEqual(
            manifest["benchmark"]["evidence_class"],
            "exploratory-nonreportable",
        )
        self.assertFalse(
            manifest["benchmark"][
                "publication_grade_external_schedule"
            ]
        )
        method = manifest["workloads"][0]["correctness"][
            "directional_oracle"
        ]["method"]
        self.assertEqual(
            method,
            {
                "schema": EXPORTER.DIRECTIONAL_ORACLE_SCHEMA,
                "version": EXPORTER.DIRECTIONAL_ORACLE_SCHEMA_VERSION,
                "methods": EXPORTER.DIRECTIONAL_ORACLE_METHODS,
                "independent_components": list(
                    EXPORTER.DIRECTIONAL_PUBLIC_INDEPENDENT_COMPONENTS
                ),
                "shared_transport_components": list(
                    EXPORTER.DIRECTIONAL_PUBLIC_SHARED_TRANSPORT_COMPONENTS
                ),
            },
        )
        readme = (self.fixture.output / "README.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "independent for directional\nclustering, distance evaluation, "
            "threshold evaluation, and root construction,\nwhile reusing "
            "the audited SAM, QNAME/UMI, alignment-group, header, and\n"
            "external-sort transport",
            readme,
        )
        self.assertNotIn("independent directional-collapse oracle", readme)
        self.assertEqual(len(manifest["harness"]), len(EXPORTER.HARNESS_PATHS))
        self.assertEqual(
            set(manifest["builds"]), {"upstream", "dumi"}
        )
        self.assertTrue(self.fixture.private_receipt.is_file())
        self.assertEqual(
            os.stat(self.fixture.private_receipt).st_mode & 0o777,
            0o600,
        )
        private_receipt = json.loads(
            self.fixture.private_receipt.read_text(encoding="utf-8")
        )
        self.assertEqual(private_receipt["evidence_set_id"], EVIDENCE_SET_ID)
        self.assertEqual(
            private_receipt["private_inputs"]["alias_map_sha256"],
            sha256(self.fixture.alias_map),
        )
        self.assertEqual(
            private_receipt["private_inputs"]["denylist_sha256"],
            sha256(self.fixture.denylist),
        )
        self.assertEqual(
            private_receipt["restricted_source"]["manifest_sha256"],
            sha256(self.fixture.bundle / "MANIFEST.sha256"),
        )
        self.assertEqual(
            private_receipt["public_projection"]["files"],
            {
                filename: sha256(self.fixture.output / filename)
                for filename in EXPORTER.PUBLIC_FILES
            },
        )
        self.assertEqual(
            private_receipt["public_projection"]["tree_sha256"],
            EXPORTER.public_tree_sha256(
                private_receipt["public_projection"]["files"]
            ),
        )
        self.assertNotIn(self.fixture.private_receipt.name, EXPORTER.PUBLIC_FILES)

        for line in (
            self.fixture.output / "SHA256SUMS"
        ).read_text(encoding="utf-8").splitlines():
            digest, filename = line.split("  ", 1)
            self.assertEqual(sha256(self.fixture.output / filename), digest)
        self.assertEqual(
            {
                line.split("  ", 1)[1]
                for line in (
                    self.fixture.output / "SHA256SUMS"
                ).read_text(encoding="utf-8").splitlines()
            },
            set(EXPORTER.CHECKSUMMED_PUBLIC_FILES),
        )

    def test_lowercase_private_token_and_branding_are_rejected(self) -> None:
        for description in (
            f"Neutral panel for {PRIVATE_TOKEN}",
            f"Neutral panel by {PRIVATE_BRAND.swapcase()}",
        ):
            with self.subTest(description=description):
                completed = self.fixture.run(description=description)
                self.assertNotEqual(completed.returncode, 0)
                self.assertFalse(self.fixture.output.exists())
                self.assertFalse(
                    any(
                        path.name.startswith(f".{self.fixture.output.name}.tmp-")
                        for path in self.root.iterdir()
                    )
                )

    def test_private_absolute_path_is_rejected(self) -> None:
        for description in (
            f"Neutral panel from {PRIVATE_PATH}",
            f"Neutral panel location:{PRIVATE_PATH}",
        ):
            with self.subTest(description=description):
                completed = self.fixture.run(description=description)
                self.assertNotEqual(completed.returncode, 0)
                self.assertFalse(self.fixture.output.exists())

    def test_private_bam_and_index_hashes_are_rejected_if_injected(self) -> None:
        for digest in (PRIVATE_BAM_SHA256, PRIVATE_BAI_SHA256):
            with self.subTest(digest=digest[:8]):
                completed = self.fixture.run(
                    description=f"Neutral panel fingerprint {digest}"
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertFalse(self.fixture.output.exists())

    def test_public_tree_rejects_unexpected_sam_and_binary_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory_string:
            directory = Path(directory_string)
            (directory / "unexpected.sam").write_text(
                "@HD\tVN:1.6\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                EXPORTER.ExportError, "unexpected file"
            ):
                EXPORTER.validate_public_tree(directory)
        with tempfile.TemporaryDirectory() as directory_string:
            directory = Path(directory_string)
            (directory / "README.md").write_bytes(b"public\x00private")
            with self.assertRaisesRegex(
                EXPORTER.ExportError, "binary or NUL"
            ):
                EXPORTER.validate_public_tree(directory)

    def test_public_scanner_normalizes_semantic_unicode_and_hash_runs(
        self,
    ) -> None:
        cases = (
            (
                "manifest.public.json",
                json.dumps({"value": r"review\private\sample.bam"}) + "\n",
                EXPORTER.PrivateDenylist(
                    paths=(r"review\private\sample.bam",)
                ),
            ),
            (
                "README.md",
                "ｈｔｔｐｓ：／／unapproved.example/path\n",
                EXPORTER.PrivateDenylist(),
            ),
            (
                "README.md",
                (
                    EXPORTER.BUILTIN_FORBIDDEN_TOKENS[0][:4]
                    + "\u200b"
                    + EXPORTER.BUILTIN_FORBIDDEN_TOKENS[0][4:]
                    + "\n"
                ),
                EXPORTER.PrivateDenylist(),
            ),
            (
                "README.md",
                ("a" * 65) + "\n",
                EXPORTER.PrivateDenylist(),
            ),
        )
        for filename, text, denylist in cases:
            with self.subTest(filename=filename, text=text[:20]):
                with tempfile.TemporaryDirectory(dir=self.root) as directory:
                    root = Path(directory)
                    (root / filename).write_text(text, encoding="utf-8")
                    with self.assertRaises(EXPORTER.ExportError):
                        EXPORTER.validate_public_tree(root, denylist)

    def test_per_run_receipts_are_directly_bound_to_measurements(self) -> None:
        mutations = (
            (
                "command_file",
                lambda path: path.write_text(
                    path.read_text(encoding="utf-8").rstrip("\n")
                    + " --unexpected\n",
                    encoding="utf-8",
                ),
            ),
            (
                "time.tsv",
                lambda path: path.write_text(
                    "2.0\t999\t0.1\t90%\t100000\t0\n",
                    encoding="utf-8",
                ),
            ),
            (
                "monotonic-wall-seconds.txt",
                lambda path: path.write_text("999\n", encoding="utf-8"),
            ),
            (
                "inspection.json",
                lambda path: write_json(
                    path,
                    {
                        **json.loads(path.read_text(encoding="utf-8")),
                        "semantic_sha256": PRIVATE_OUTPUT_SHA256,
                    },
                ),
            ),
            (
                "stdout_file",
                lambda path: path.write_text(
                    "not redacted\n", encoding="utf-8"
                ),
            ),
        )
        for name, mutate in mutations:
            with self.subTest(receipt=name):
                with tempfile.TemporaryDirectory(dir=self.root) as directory:
                    fixture = RestrictedBundleFixture(Path(directory))
                    row = read_tsv(fixture.bundle / "measurements.tsv")[0]
                    path = (
                        fixture.bundle / row[name]
                        if name.endswith("_file")
                        else (
                            fixture.bundle / row["command_file"]
                        ).parent
                        / name
                    )
                    mutate(path)
                    path.chmod(0o600)
                    fixture.reseal()
                    completed = fixture.run()
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertFalse(fixture.output.exists())
                    self.assertFalse(fixture.private_receipt.exists())

    def test_ready_command_requires_literal_shell_operators(self) -> None:
        row = next(
            candidate
            for candidate in read_tsv(
                self.fixture.bundle / "measurements.tsv"
            )
            if candidate["stage"] == "end_to_end_ready"
        )
        command_path = self.fixture.bundle / row["command_file"]
        command_text = command_path.read_text(encoding="utf-8")
        self.assertIn(" && ", command_text)
        command_path.write_text(
            command_text.replace(" && ", ' "&&" '),
            encoding="utf-8",
        )
        command_path.chmod(0o600)
        self.fixture.reseal()
        completed = self.fixture.run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("runner contract", completed.stderr)
        self.assertFalse(self.fixture.output.exists())
        self.assertFalse(self.fixture.private_receipt.exists())

    def test_leading_hyphen_umi_separator_is_not_publishable(self) -> None:
        manifest_path = self.fixture.bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["workloads"][0]["umi_separator"] = "-"
        manifest["external_inputs"][0]["umi_separator"] = "-"
        write_json(manifest_path, manifest)
        hashes_path = (
            self.fixture.bundle
            / "inputs"
            / "external"
            / SOURCE_ID
            / "hashes.json"
        )
        hashes = json.loads(hashes_path.read_text(encoding="utf-8"))
        hashes["umi_separator"] = "-"
        write_json(hashes_path, hashes)
        self.fixture.reseal()
        completed = self.fixture.run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("separator", completed.stderr)
        self.assertFalse(self.fixture.output.exists())

    def test_tampered_or_invalid_restricted_manifest_is_rejected(self) -> None:
        (self.fixture.bundle / "manifest.json").write_text(
            (
                self.fixture.bundle / "manifest.json"
            ).read_text(encoding="utf-8")
            + " ",
            encoding="utf-8",
        )
        completed = self.fixture.run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("MANIFEST checksum", completed.stderr)
        self.assertFalse(self.fixture.output.exists())

    def test_unexpected_input_field_is_rejected_after_valid_reseal(self) -> None:
        path = self.fixture.bundle / "measurements.tsv"
        lines = path.read_text(encoding="utf-8").splitlines()
        lines[0] += "\tunexpected_private_field"
        for index in range(1, len(lines)):
            lines[index] += "\tvalue"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.fixture.reseal()
        completed = self.fixture.run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("expected schema", completed.stderr)
        self.assertFalse(self.fixture.output.exists())

    def test_failed_correctness_is_rejected_after_valid_reseal(self) -> None:
        path = self.fixture.bundle / "correctness.tsv"
        with path.open("r", encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        rows[0]["correctness_status"] = "fail"
        rows[0]["issue_count"] = "1"
        rows[0]["issues"] = "synthetic failure"
        write_tsv(path, EXPORTER.CORRECTNESS_INPUT_FIELDS, rows)
        self.fixture.reseal()
        completed = self.fixture.run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("failed cell", completed.stderr)
        self.assertFalse(self.fixture.output.exists())

    def test_alias_map_must_be_complete_neutral_and_unique(self) -> None:
        cases = (
            {},
            {SOURCE_ID: "order-secret"},
            {SOURCE_ID: PUBLIC_ALIAS, "extra-source": "panel-se-02"},
        )
        for aliases in cases:
            with self.subTest(aliases=aliases):
                self.fixture.write_aliases(aliases)
                completed = self.fixture.run()
                self.assertNotEqual(completed.returncode, 0)
                self.assertFalse(self.fixture.output.exists())

    def test_existing_destination_is_preserved(self) -> None:
        self.fixture.output.mkdir()
        sentinel = self.fixture.output / "sentinel.txt"
        sentinel.write_text("keep\n", encoding="utf-8")
        completed = self.fixture.run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")
        self.assertEqual({path.name for path in self.fixture.output.iterdir()}, {"sentinel.txt"})

    def test_private_denylist_is_mandatory_but_may_be_explicitly_empty(
        self,
    ) -> None:
        missing = self.fixture.run(include_denylist=False)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("--private-denylist", missing.stderr)
        self.assertFalse(self.fixture.output.exists())
        self.assertFalse(self.fixture.private_receipt.exists())

        self.fixture.write_denylist(tokens=[], paths=[], hashes=[])
        completed = self.fixture.run()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(self.fixture.private_receipt.is_file())

    def test_unicode_denylist_token_is_rejected_after_normalization(self) -> None:
        environment_path = self.fixture.bundle / "environment.json"
        environment = json.loads(environment_path.read_text(encoding="utf-8"))
        environment["platform"] = "Linux Cafe\u0301Brand x86_64"
        write_json(environment_path, environment)
        self.fixture.write_denylist(
            tokens=["Caf\u00e9Brand"],
            paths=[],
            hashes=[],
        )
        self.fixture.reseal()
        completed = self.fixture.run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("private source token", completed.stderr)
        self.assertFalse(self.fixture.output.exists())

    def test_unapproved_uri_is_rejected_without_denylist_match(self) -> None:
        environment_path = self.fixture.bundle / "environment.json"
        environment = json.loads(environment_path.read_text(encoding="utf-8"))
        environment["platform"] = "Linux https://private.example/order x86_64"
        write_json(environment_path, environment)
        self.fixture.write_denylist(tokens=[], paths=[], hashes=[])
        self.fixture.reseal()
        completed = self.fixture.run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(self.fixture.output.exists())

    def test_dependency_url_and_hash_cannot_be_spoofed(self) -> None:
        manifest_path = self.fixture.bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["dependencies"][0]["url"] = (
            "https://repo.maven.apache.org/maven2/private/order.jar"
        )
        write_json(manifest_path, manifest)
        self.fixture.reseal()
        completed = self.fixture.run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("dependency inventory", completed.stderr)
        self.assertFalse(self.fixture.output.exists())

        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            fixture = RestrictedBundleFixture(Path(directory))
            manifest_path = fixture.bundle / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["dependencies"][0]["sha256"] = PRIVATE_SEMANTIC_SHA256
            manifest["dependency_files"][0][
                "sha256"
            ] = PRIVATE_SEMANTIC_SHA256
            write_json(manifest_path, manifest)
            fixture.reseal()
            completed = fixture.run()
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("dependency inventory", completed.stderr)
            self.assertFalse(fixture.output.exists())

    def test_dependency_inventory_cannot_omit_locked_runtime_jar(self) -> None:
        manifest_path = self.fixture.bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["dependencies"] = manifest["dependencies"][:1]
        manifest["dependency_files"] = manifest["dependency_files"][:1]
        write_json(manifest_path, manifest)
        self.fixture.reseal()
        completed = self.fixture.run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("dependency inventory is incomplete", completed.stderr)
        self.assertFalse(self.fixture.output.exists())

    def test_dependency_bytes_are_bound_to_the_committed_lock(self) -> None:
        dependency_path = (
            self.fixture.bundle
            / "dependencies"
            / "htsjdk-3.0.5.jar"
        )
        dependency_path.write_bytes(b"resealed replacement dependency\n")
        dependency_path.chmod(0o600)
        replacement = sha256(dependency_path)
        manifest_path = self.fixture.bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["dependencies"][0]["sha256"] = replacement
        manifest["dependency_files"][0]["sha256"] = replacement
        write_json(manifest_path, manifest)
        self.fixture.reseal()
        completed = self.fixture.run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("dependency inventory", completed.stderr)
        self.assertFalse(self.fixture.output.exists())

    def test_archived_dependency_lock_is_exact(self) -> None:
        lock = (
            self.fixture.bundle / "sources" / "dumi" / "dependencies.lock"
        )
        lock.write_text("# forged dependency lock\n", encoding="utf-8")
        lock.chmod(0o600)
        self.fixture.reseal()
        completed = self.fixture.run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("dependency lock provenance", completed.stderr)
        self.assertFalse(self.fixture.output.exists())

    def test_cluster_tag_heap_source_contract_is_validated(self) -> None:
        manifest_path = self.fixture.bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["config"]["cluster_tag_xmx_source"] = "forged-source"
        write_json(manifest_path, manifest)
        self.fixture.reseal()
        completed = self.fixture.run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("configuration", completed.stderr)

    def test_external_mode_synthetic_configuration_is_empty(self) -> None:
        cases: dict[str, object] = {
            "hotspot_families": 1,
            "moderate_families_per_group": 1,
            "moderate_groups": 1,
            "paired_pairs_per_reference": 1,
            "paired_references": [1],
            "seed": 1,
            "selected_workloads": ["sparse"],
            "sparse_records": [1],
        }
        for field, value in cases.items():
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory(dir=self.root) as directory:
                    fixture = RestrictedBundleFixture(Path(directory))
                    manifest_path = fixture.bundle / "manifest.json"
                    manifest = json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                    manifest["config"][field] = value
                    write_json(manifest_path, manifest)
                    fixture.reseal()
                    completed = fixture.run()
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn("configuration", completed.stderr)

    def test_duplicate_external_bam_content_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            EXPORTER.ExportError, "reuse BAM content"
        ):
            EXPORTER.require_unique_external_input_hashes(
                [
                    {"sha256": PRIVATE_BAM_SHA256},
                    {"sha256": PRIVATE_BAM_SHA256},
                ]
            )

    def test_method_and_environment_identities_are_bound(self) -> None:
        manifest_path = self.fixture.bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["harness_files"][0]["sha256"] = "f" * 64
        write_json(manifest_path, manifest)
        self.fixture.reseal()
        completed = self.fixture.run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("harness inventory", completed.stderr)

        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            fixture = RestrictedBundleFixture(Path(directory))
            manifest_path = fixture.bundle / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["subprocess_environment"]["LANG"] = "en_US.UTF-8"
            write_json(manifest_path, manifest)
            fixture.reseal()
            completed = fixture.run()
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("environment policies", completed.stderr)

        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            fixture = RestrictedBundleFixture(Path(directory))
            archived = (
                fixture.bundle
                / "sources"
                / "dumi"
                / EXPORTER.HARNESS_REPOSITORY_PATHS[0]
            )
            archived.write_text("tampered archive\n", encoding="utf-8")
            fixture.reseal()
            completed = fixture.run()
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("harness commit binding", completed.stderr)

    def test_public_exporter_must_be_source_bound_and_committed(self) -> None:
        invalid = {
            "repository_url": EXPORTER.DUMI_PUBLIC_URL,
            "repository_path": (
                "scripts/benchmark/export_public_external.py"
            ),
            "commit_sha": DUMI_SHA,
            "state": "untracked",
            "tracked": False,
            "commit_blob_sha256": None,
            "matches_commit": False,
        }
        for publication_grade in (False, True):
            with self.subTest(publication_grade=publication_grade):
                with self.assertRaisesRegex(
                    EXPORTER.ExportError, "clean committed exporter"
                ):
                    EXPORTER.validate_exporter_source_binding(
                        publication_grade=publication_grade,
                        exporter_git=invalid,
                        exporter_sha256=PRIVATE_OUTPUT_SHA256,
                        harness_commit_sha=DUMI_SHA,
                    )

    def test_build_sources_and_commands_are_commit_bound(self) -> None:
        cases = ("upstream-source", "dumi-source", "build-command")
        for case in cases:
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory(dir=self.root) as directory:
                    fixture = RestrictedBundleFixture(Path(directory))
                    manifest_path = fixture.bundle / "manifest.json"
                    manifest = json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                    if case == "build-command":
                        command = (
                            fixture.bundle
                            / "build-commands"
                            / "dumi"
                            / "command.txt"
                        )
                        command.write_text(
                            "'<JAVAC>' --release 11 forged.java\n",
                            encoding="utf-8",
                        )
                        command.chmod(0o600)
                        expected_message = "build command"
                    else:
                        label = case.removesuffix("-source")
                        source_root = (
                            fixture.bundle
                            / "sources"
                            / label
                            / "src"
                            / "umicollapse"
                        )
                        source = sorted(source_root.rglob("*.java"))[0]
                        source.write_bytes(
                            source.read_bytes() + b"\n// resealed substitution\n"
                        )
                        source.chmod(0o600)
                        manifest["builds"][label][
                            "source_tree_sha256"
                        ] = EXPORTER.sha256_tree(source_root)
                        write_json(manifest_path, manifest)
                        expected_message = "build identity"
                    fixture.reseal()
                    completed = fixture.run()
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn(expected_message, completed.stderr)
                    self.assertFalse(fixture.output.exists())

    def test_directional_receipt_content_is_validated(self) -> None:
        receipt_path = (
            self.fixture.bundle
            / "oracles"
            / "external"
            / SOURCE_ID
            / "directional-oracle-receipt.json"
        )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["source_oracle"]["membership_partition_sha256"] = (
            PRIVATE_ALTERNATE_OUTPUT_SHA256
        )
        write_json(receipt_path, receipt)
        manifest_path = self.fixture.bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["workloads"][0]["directional_oracle_gate"][
            "receipt_sha256"
        ] = sha256(receipt_path)
        write_json(manifest_path, manifest)
        self.fixture.reseal()
        completed = self.fixture.run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("directional-oracle", completed.stderr)
        self.assertFalse(self.fixture.output.exists())

    def test_directional_method_identity_cannot_be_forged(self) -> None:
        receipt_path = (
            self.fixture.bundle
            / "oracles"
            / "external"
            / SOURCE_ID
            / "directional-oracle-receipt.json"
        )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["methods"]["membership_oracle"] = "forged-method-v1"
        write_json(receipt_path, receipt)
        manifest_path = self.fixture.bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        gate = manifest["workloads"][0]["directional_oracle_gate"]
        gate["methods"] = receipt["methods"]
        gate["receipt_sha256"] = sha256(receipt_path)
        write_json(manifest_path, manifest)
        self.fixture.reseal()
        completed = self.fixture.run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("directional-oracle receipt", completed.stderr)

    def test_directional_source_size_and_record_arithmetic_are_bound(
        self,
    ) -> None:
        cases = ("input_bytes", "input_records", "record_arithmetic")
        for case in cases:
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory(dir=self.root) as directory:
                    fixture = RestrictedBundleFixture(Path(directory))
                    receipt_path = (
                        fixture.bundle
                        / "oracles"
                        / "external"
                        / SOURCE_ID
                        / "directional-oracle-receipt.json"
                    )
                    receipt = json.loads(
                        receipt_path.read_text(encoding="utf-8")
                    )
                    source = receipt["source_oracle"]
                    if case == "input_bytes":
                        source["input_bytes"] += 1
                    elif case == "input_records":
                        source["input_records"] += 1
                        source["excluded_unmapped"] += 1
                    else:
                        source["excluded_unmapped"] += 1
                    write_json(receipt_path, receipt)
                    manifest_path = fixture.bundle / "manifest.json"
                    manifest = json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                    manifest["workloads"][0][
                        "directional_oracle_gate"
                    ]["receipt_sha256"] = sha256(receipt_path)
                    write_json(manifest_path, manifest)
                    fixture.reseal()
                    completed = fixture.run()
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn("directional-oracle", completed.stderr)

    def test_directional_metric_geometry_is_nonempty_and_ordered(self) -> None:
        cases = {
            "alignment_groups": 0,
            "clusters": 0,
            "umi_memberships": 0,
            "max_umi_memberships_per_cluster": 0,
            "membership_partition_bytes": 0,
            "rooted_partition_bytes": 0,
            "alignment_umi_frequency_multiset_bytes": 0,
            "record_key_bytes": 0,
            "reference_sequences": 0,
        }
        for field, value in cases.items():
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory(dir=self.root) as directory:
                    fixture = RestrictedBundleFixture(Path(directory))
                    receipt_path = (
                        fixture.bundle
                        / "oracles"
                        / "external"
                        / SOURCE_ID
                        / "directional-oracle-receipt.json"
                    )
                    receipt = json.loads(
                        receipt_path.read_text(encoding="utf-8")
                    )
                    receipt["source_oracle"][field] = value
                    write_json(receipt_path, receipt)
                    manifest_path = fixture.bundle / "manifest.json"
                    manifest = json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                    manifest["workloads"][0][
                        "directional_oracle_gate"
                    ]["receipt_sha256"] = sha256(receipt_path)
                    write_json(manifest_path, manifest)
                    fixture.reseal()
                    completed = fixture.run()
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn("directional-oracle", completed.stderr)

    def test_external_provenance_ledger_receipts_are_strictly_bound(
        self,
    ) -> None:
        cases = ("top-level-flags", "per-input-copy", "workload-count")
        for case in cases:
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory(dir=self.root) as directory:
                    fixture = RestrictedBundleFixture(Path(directory))
                    manifest_path = fixture.bundle / "manifest.json"
                    manifest = json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                    top = manifest["external_provenance_ledger"]
                    per_input = manifest["external_inputs"][0][
                        "provenance_ledger"
                    ]
                    if case == "top-level-flags":
                        top["authorization_confirmed"] = False
                    elif case == "per-input-copy":
                        per_input["sha256"] = "6" * 64
                    else:
                        top["workload_count"] = 2
                        per_input["workload_count"] = 2
                    write_json(manifest_path, manifest)
                    fixture.reseal()
                    completed = fixture.run()
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn("provenance ledger", completed.stderr)

    def test_summary_private_identity_is_bound_to_measurements(self) -> None:
        summary_path = self.fixture.bundle / "summary.tsv"
        rows = read_tsv(summary_path)
        rows[0]["input_sha256"] = "0" * 64
        write_tsv(summary_path, EXPORTER.SUMMARY_INPUT_FIELDS, rows)
        self.fixture.reseal()
        completed = self.fixture.run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("summary identity", completed.stderr)

    def test_external_input_identity_is_bound_to_measurements(self) -> None:
        replacement = "0" * 64
        manifest_path = self.fixture.bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        external = manifest["external_inputs"][0]
        external["sha256"] = replacement
        external["private_timing_snapshot"]["sha256"] = replacement
        manifest["workloads"][0]["directional_oracle_gate"][
            "input_sha256"
        ] = replacement
        directional_path = (
            self.fixture.bundle
            / "oracles"
            / "external"
            / SOURCE_ID
            / "directional-oracle-receipt.json"
        )
        directional = json.loads(
            directional_path.read_text(encoding="utf-8")
        )
        directional["source_oracle"]["input_sha256"] = replacement
        write_json(directional_path, directional)
        manifest["workloads"][0]["directional_oracle_gate"][
            "receipt_sha256"
        ] = sha256(directional_path)
        write_json(manifest_path, manifest)
        hashes_path = (
            self.fixture.bundle
            / "inputs"
            / "external"
            / SOURCE_ID
            / "hashes.json"
        )
        hashes = json.loads(hashes_path.read_text(encoding="utf-8"))
        hashes["bam"]["sha256"] = replacement
        write_json(hashes_path, hashes)
        self.fixture.reseal()
        completed = self.fixture.run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("workload inputs", completed.stderr)
        self.assertFalse(self.fixture.output.exists())

    def test_retained_oracle_identity_is_bound_to_measurements(self) -> None:
        hashes_path = (
            self.fixture.bundle
            / "inputs"
            / "external"
            / SOURCE_ID
            / "hashes.json"
        )
        hashes = json.loads(hashes_path.read_text(encoding="utf-8"))
        for oracle in hashes["oracles"].values():
            oracle["semantic_sha256"] = PRIVATE_OUTPUT_SHA256
        write_json(hashes_path, hashes)
        self.fixture.reseal()
        completed = self.fixture.run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("retained", completed.stderr)
        self.assertFalse(self.fixture.output.exists())

    def test_builtin_public_branding_gate_cannot_be_emptied(self) -> None:
        forbidden = EXPORTER.BUILTIN_FORBIDDEN_TOKENS[0]
        self.fixture.write_denylist(tokens=[], paths=[], hashes=[])
        completed = self.fixture.run(
            description=f"{forbidden} external performance panel"
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(self.fixture.output.exists())

        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            fixture = RestrictedBundleFixture(Path(directory))
            fixture.write_denylist(tokens=[], paths=[], hashes=[])
            fixture.output = fixture.root / f"{forbidden}-public"
            completed = fixture.run()
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("directory name", completed.stderr)
            self.assertFalse(fixture.output.exists())

        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            fixture = RestrictedBundleFixture(Path(directory))
            fixture.output = fixture.root / PRIVATE_BAM_SHA256
            completed = fixture.run()
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("directory name", completed.stderr)
            self.assertFalse(fixture.output.exists())

    def test_incomplete_treatment_matrix_is_rejected(self) -> None:
        for filename, fields in (
            ("design.tsv", EXPORTER.DESIGN_INPUT_FIELDS),
            ("measurements.tsv", EXPORTER.MEASUREMENT_INPUT_FIELDS),
            ("summary.tsv", EXPORTER.SUMMARY_INPUT_FIELDS),
            ("comparisons.tsv", EXPORTER.COMPARISON_INPUT_FIELDS),
            ("correctness.tsv", EXPORTER.CORRECTNESS_INPUT_FIELDS),
        ):
            path = self.fixture.bundle / filename
            rows = [
                row
                for row in read_tsv(path)
                if not (
                    row["implementation"] == "dumi"
                    and row["mode"] == "on"
                )
            ]
            write_tsv(path, fields, rows)
        self.fixture.reseal()
        completed = self.fixture.run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("treatment matrix", completed.stderr)
        self.assertFalse(self.fixture.output.exists())

    def test_route_oracle_and_receipt_contracts_are_rejected(self) -> None:
        cases = (
            ("actual_route", "streaming"),
            ("oracle_implementation", "canonical-upstream"),
            ("directional_oracle_receipt", "oracles/other/receipt.json"),
        )
        for field, value in cases:
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory(dir=self.root) as directory:
                    fixture = RestrictedBundleFixture(Path(directory))
                    path = fixture.bundle / "measurements.tsv"
                    rows = read_tsv(path)
                    row = next(
                        item
                        for item in rows
                        if item["implementation"] == "dumi"
                        and item["mode"] == "off"
                    )
                    row[field] = value
                    write_tsv(
                        path, EXPORTER.MEASUREMENT_INPUT_FIELDS, rows
                    )
                    fixture.reseal()
                    completed = fixture.run()
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertFalse(fixture.output.exists())

    def test_cross_implementation_claim_must_match_oracle_evidence(
        self,
    ) -> None:
        path = self.fixture.bundle / "measurements.tsv"
        rows = read_tsv(path)
        for row in rows:
            row["cross_implementation_exact_match"] = "False"
        write_tsv(path, EXPORTER.MEASUREMENT_INPUT_FIELDS, rows)
        self.fixture.reseal()
        completed = self.fixture.run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("retained oracles", completed.stderr)
        self.assertFalse(self.fixture.output.exists())

    def test_bounded_cross_checks_can_pass_when_exact_outputs_differ(
        self,
    ) -> None:
        self.fixture.make_cross_implementation_inexact()
        completed = self.fixture.run()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        measurements = read_tsv(
            self.fixture.output / "measurements.public.tsv"
        )
        self.assertEqual(
            {row["cross_implementation_exact_match"] for row in measurements},
            {"false"},
        )
        correctness = read_tsv(
            self.fixture.output / "correctness.public.tsv"
        )
        self.assertEqual(
            {
                row["directional_oracle_gate_pass"]
                for row in correctness
            },
            {"true"},
        )
        self.assertEqual(
            {
                row["cross_implementation_exact_match"]
                for row in correctness
            },
            {"false"},
        )
        manifest = json.loads(
            (self.fixture.output / "manifest.public.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(
            manifest["correctness"][
                "cross_implementation_exact_match_all"
            ]
        )
        self.assertTrue(
            manifest["workloads"][0]["correctness"][
                "cross_implementation_bounded_diagnostic_match"
            ]
        )

    def test_output_count_mismatch_is_exported_as_noncomparable(
        self,
    ) -> None:
        self.fixture.make_cross_output_count_mismatch()
        completed = self.fixture.run()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        comparisons = read_tsv(
            self.fixture.output / "comparisons.public.tsv"
        )
        self.assertEqual(
            {row["comparability_status"] for row in comparisons},
            {"not_comparable"},
        )
        self.assertEqual(
            {row["successful_pairs"] for row in comparisons}, {"0"}
        )
        self.assertEqual(
            {row["noncomparable_pairs"] for row in comparisons}, {"1"}
        )
        self.assertEqual(
            {
                row[f"{metric}_n"]
                for row in comparisons
                for metric in EXPORTER.COMPARISON_METRICS
            },
            {"0"},
        )
        self.assertTrue(
            all(
                row[f"{metric}_{statistic}"] == ""
                for row in comparisons
                for metric in EXPORTER.COMPARISON_METRICS
                for statistic in ("median", "min", "max", "range", "mad")
            )
        )
        summaries = read_tsv(
            self.fixture.output / "summary.public.tsv"
        )
        self.assertEqual(
            {row["comparability_status"] for row in summaries},
            {"not_comparable"},
        )
        manifest = json.loads(
            (self.fixture.output / "manifest.public.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(
            manifest["correctness"]["performance_comparable_all"]
        )

    def test_upstream_pairwise_difference_is_disclosed_not_required(
        self,
    ) -> None:
        self.fixture.make_upstream_pairwise_diagnostic_difference()
        completed = self.fixture.run()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        correctness = read_tsv(
            self.fixture.output / "correctness.public.tsv"
        )
        self.assertEqual(
            {row["directional_oracle_gate_pass"] for row in correctness},
            {"true"},
        )
        self.assertEqual(
            {
                row["canonical_upstream_oracle_partition_equivalent"]
                for row in correctness
            },
            {"false"},
        )
        self.assertEqual(
            {
                row["pairwise_cluster_partition_equivalent"]
                for row in correctness
            },
            {"false"},
        )
        self.assertEqual(
            {
                row[
                    "cross_implementation_bounded_diagnostic_match"
                ]
                for row in correctness
            },
            {"false"},
        )
        self.assertEqual(
            {row["upstream_agreement_required"] for row in correctness},
            {"false"},
        )

    def test_paired_ineligible_three_treatment_schedule_exports(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            fixture = RestrictedBundleFixture(
                Path(directory), paired=True
            )
            completed = fixture.run(
                description="Neutral paired-end demonstration panel"
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            design = read_tsv(fixture.output / "design.public.tsv")
            self.assertEqual(
                {(row["implementation"], row["mode"]) for row in design},
                {
                    ("canonical-upstream", "legacy"),
                    ("dumi", "off"),
                    ("dumi", "auto"),
                },
            )
            self.assertEqual({row["workload_id"] for row in design}, {"panel-pe-01"})
            manifest = json.loads(
                (fixture.output / "manifest.public.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                manifest["benchmark"]["evidence_class"],
                "exploratory-nonreportable",
            )
            self.assertEqual(
                manifest["workloads"][0]["timing_design"]["order_family"],
                "cyclic-latin-fallback-nonreportable",
            )

    def test_summary_and_comparison_aggregates_are_recomputed(self) -> None:
        cases = (
            (
                "summary.tsv",
                EXPORTER.SUMMARY_INPUT_FIELDS,
                "elapsed_s_median",
                "999",
                "summary",
            ),
            (
                "comparisons.tsv",
                EXPORTER.COMPARISON_INPUT_FIELDS,
                "elapsed_speedup_median",
                "999",
                "comparisons",
            ),
        )
        for filename, fields, field, value, message in cases:
            with self.subTest(filename=filename):
                with tempfile.TemporaryDirectory(dir=self.root) as directory:
                    fixture = RestrictedBundleFixture(Path(directory))
                    path = fixture.bundle / filename
                    rows = read_tsv(path)
                    rows[0][field] = value
                    write_tsv(path, fields, rows)
                    fixture.reseal()
                    completed = fixture.run()
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn(message, completed.stderr)
                    self.assertFalse(fixture.output.exists())

    def test_private_input_permissions_are_enforced(self) -> None:
        self.fixture.alias_map.chmod(0o644)
        completed = self.fixture.run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("group or other access", completed.stderr)
        self.assertFalse(self.fixture.output.exists())

        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            fixture = RestrictedBundleFixture(Path(directory))
            fixture.bundle.chmod(0o755)
            completed = fixture.run()
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("group or other access", completed.stderr)
            self.assertFalse(fixture.output.exists())

    def test_dangling_output_symlink_is_preserved_and_rejected(self) -> None:
        target = self.root / "does-not-exist"
        self.fixture.output.symlink_to(target)
        completed = self.fixture.run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertTrue(self.fixture.output.is_symlink())
        self.assertEqual(os.readlink(self.fixture.output), str(target))
        self.assertFalse(self.fixture.private_receipt.exists())

    def test_private_receipt_is_no_overwrite_and_publication_is_atomic(
        self,
    ) -> None:
        self.fixture.private_receipt.write_text(
            "preserve\n", encoding="utf-8"
        )
        self.fixture.private_receipt.chmod(0o600)
        completed = self.fixture.run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(
            self.fixture.private_receipt.read_text(encoding="utf-8"),
            "preserve\n",
        )
        self.assertFalse(self.fixture.output.exists())

        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            fixture = RestrictedBundleFixture(Path(directory))
            with mock.patch.object(
                EXPORTER.os,
                "link",
                side_effect=OSError("synthetic publication race"),
            ):
                with self.assertRaisesRegex(
                    EXPORTER.ExportError,
                    "could not publish private export receipt",
                ):
                    EXPORTER.export_public(
                        bundle=fixture.bundle,
                        output=fixture.output,
                        alias_map=fixture.alias_map,
                        panel_description=(
                            "Neutral single-end demonstration panel"
                        ),
                        denylist_path=fixture.denylist,
                        private_export_receipt=fixture.private_receipt,
                        evidence_set_id=EVIDENCE_SET_ID,
                    )
            self.assertFalse(fixture.output.exists())
            self.assertFalse(fixture.private_receipt.exists())
            self.assertFalse(
                any(".tmp-" in path.name for path in fixture.root.iterdir())
            )

    def test_link_effect_then_keyboard_interrupt_is_reconciled(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            fixture = RestrictedBundleFixture(Path(directory))
            original_link = EXPORTER.os.link

            def link_then_interrupt(
                source: Path,
                destination: Path,
                *args: object,
                **kwargs: object,
            ) -> None:
                original_link(source, destination, *args, **kwargs)
                raise KeyboardInterrupt("synthetic post-link interrupt")

            with mock.patch.object(
                EXPORTER.os,
                "link",
                side_effect=link_then_interrupt,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    EXPORTER.export_public(
                        bundle=fixture.bundle,
                        output=fixture.output,
                        alias_map=fixture.alias_map,
                        panel_description=(
                            "Neutral single-end demonstration panel"
                        ),
                        denylist_path=fixture.denylist,
                        private_export_receipt=fixture.private_receipt,
                        evidence_set_id=EVIDENCE_SET_ID,
                    )
            self.assertFalse(fixture.output.exists())
            self.assertFalse(fixture.private_receipt.exists())
            self.assertFalse(
                any(".tmp-" in path.name for path in fixture.root.iterdir())
            )

    def test_rename_effect_then_keyboard_interrupt_is_reconciled(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            fixture = RestrictedBundleFixture(Path(directory))
            original_rename = EXPORTER.rename_noreplace

            def rename_then_interrupt(
                source: Path,
                destination: Path,
            ) -> None:
                original_rename(source, destination)
                raise KeyboardInterrupt("synthetic post-rename interrupt")

            with mock.patch.object(
                EXPORTER,
                "rename_noreplace",
                side_effect=rename_then_interrupt,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    EXPORTER.export_public(
                        bundle=fixture.bundle,
                        output=fixture.output,
                        alias_map=fixture.alias_map,
                        panel_description=(
                            "Neutral single-end demonstration panel"
                        ),
                        denylist_path=fixture.denylist,
                        private_export_receipt=fixture.private_receipt,
                        evidence_set_id=EVIDENCE_SET_ID,
                    )
            self.assertFalse(fixture.output.exists())
            self.assertFalse(fixture.private_receipt.exists())
            self.assertFalse(
                any(".tmp-" in path.name for path in fixture.root.iterdir())
            )

    def test_publication_system_exit_rolls_back_and_success_is_fsynced(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            fixture = RestrictedBundleFixture(Path(directory))
            with mock.patch.object(
                EXPORTER,
                "rename_noreplace",
                side_effect=SystemExit(75),
            ):
                with self.assertRaises(SystemExit):
                    EXPORTER.export_public(
                        bundle=fixture.bundle,
                        output=fixture.output,
                        alias_map=fixture.alias_map,
                        panel_description=(
                            "Neutral single-end demonstration panel"
                        ),
                        denylist_path=fixture.denylist,
                        private_export_receipt=fixture.private_receipt,
                        evidence_set_id=EVIDENCE_SET_ID,
                    )
            self.assertFalse(fixture.output.exists())
            self.assertFalse(fixture.private_receipt.exists())
            self.assertFalse(
                any(".tmp-" in path.name for path in fixture.root.iterdir())
            )

        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            fixture = RestrictedBundleFixture(Path(directory))
            with (
                mock.patch.object(
                    EXPORTER,
                    "fsync_public_stage",
                    wraps=EXPORTER.fsync_public_stage,
                ) as stage_sync,
                mock.patch.object(
                    EXPORTER,
                    "fsync_regular_file",
                    wraps=EXPORTER.fsync_regular_file,
                ) as file_sync,
                mock.patch.object(
                    EXPORTER,
                    "fsync_directory",
                    wraps=EXPORTER.fsync_directory,
                ) as directory_sync,
            ):
                EXPORTER.export_public(
                    bundle=fixture.bundle,
                    output=fixture.output,
                    alias_map=fixture.alias_map,
                    panel_description=(
                        "Neutral single-end demonstration panel"
                    ),
                    denylist_path=fixture.denylist,
                    private_export_receipt=fixture.private_receipt,
                    evidence_set_id=EVIDENCE_SET_ID,
                )
            stage_sync.assert_called_once()
            self.assertGreaterEqual(file_sync.call_count, len(EXPORTER.PUBLIC_FILES))
            self.assertGreaterEqual(directory_sync.call_count, 4)
            self.assertTrue(fixture.output.is_dir())
            self.assertTrue(fixture.private_receipt.is_file())

        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            fixture = RestrictedBundleFixture(Path(directory))
            original_rename = EXPORTER.rename_noreplace

            def race_output_creation(source: Path, destination: Path) -> None:
                destination.mkdir()
                (destination / "peer-marker").write_text(
                    "preserve\n", encoding="utf-8"
                )
                original_rename(source, destination)

            with mock.patch.object(
                EXPORTER,
                "rename_noreplace",
                side_effect=race_output_creation,
            ):
                with self.assertRaisesRegex(
                    EXPORTER.ExportError,
                    "appeared during publication",
                ):
                    EXPORTER.export_public(
                        bundle=fixture.bundle,
                        output=fixture.output,
                        alias_map=fixture.alias_map,
                        panel_description=(
                            "Neutral single-end demonstration panel"
                        ),
                        denylist_path=fixture.denylist,
                        private_export_receipt=fixture.private_receipt,
                        evidence_set_id=EVIDENCE_SET_ID,
                    )
            self.assertEqual(
                (fixture.output / "peer-marker").read_text(
                    encoding="utf-8"
                ),
                "preserve\n",
            )
            self.assertFalse(fixture.private_receipt.exists())

        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            fixture = RestrictedBundleFixture(Path(directory))
            original_unlink = Path.unlink
            failed = False

            def fail_first_temporary_unlink(
                path: Path, *args: object, **kwargs: object
            ) -> None:
                nonlocal failed
                if ".tmp-" in path.name and not failed:
                    failed = True
                    raise OSError("synthetic post-publication unlink failure")
                original_unlink(path, *args, **kwargs)

            with mock.patch.object(
                Path,
                "unlink",
                autospec=True,
                side_effect=fail_first_temporary_unlink,
            ):
                with self.assertRaisesRegex(
                    OSError, "post-publication unlink failure"
                ):
                    EXPORTER.export_public(
                        bundle=fixture.bundle,
                        output=fixture.output,
                        alias_map=fixture.alias_map,
                        panel_description=(
                            "Neutral single-end demonstration panel"
                        ),
                        denylist_path=fixture.denylist,
                        private_export_receipt=fixture.private_receipt,
                        evidence_set_id=EVIDENCE_SET_ID,
                    )
            self.assertFalse(fixture.output.exists())
            self.assertFalse(fixture.private_receipt.exists())
            self.assertFalse(
                any(".tmp-" in path.name for path in fixture.root.iterdir())
            )

        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            fixture = RestrictedBundleFixture(Path(directory))
            original_unlink = Path.unlink
            original_rmtree = EXPORTER.shutil.rmtree
            failed = False

            def fail_first_temporary_unlink(
                path: Path, *args: object, **kwargs: object
            ) -> None:
                nonlocal failed
                if ".tmp-" in path.name and not failed:
                    failed = True
                    raise OSError("synthetic post-publication unlink failure")
                original_unlink(path, *args, **kwargs)

            def leave_published_output(
                path: Path, *args: object, **kwargs: object
            ) -> None:
                if Path(path) == fixture.output:
                    return
                original_rmtree(path, *args, **kwargs)

            with (
                mock.patch.object(
                    Path,
                    "unlink",
                    autospec=True,
                    side_effect=fail_first_temporary_unlink,
                ),
                mock.patch.object(
                    EXPORTER.shutil,
                    "rmtree",
                    side_effect=leave_published_output,
                ),
            ):
                with self.assertRaisesRegex(
                    OSError, "post-publication unlink failure"
                ):
                    EXPORTER.export_public(
                        bundle=fixture.bundle,
                        output=fixture.output,
                        alias_map=fixture.alias_map,
                        panel_description=(
                            "Neutral single-end demonstration panel"
                        ),
                        denylist_path=fixture.denylist,
                        private_export_receipt=fixture.private_receipt,
                        evidence_set_id=EVIDENCE_SET_ID,
                    )
            self.assertTrue(fixture.output.is_dir())
            self.assertTrue(fixture.private_receipt.is_file())
            self.assertFalse(
                any(".tmp-" in path.name for path in fixture.root.iterdir())
            )

    def test_second_seal_verification_detects_mid_export_mutation(
        self,
    ) -> None:
        original_verify = EXPORTER.verify_restricted_bundle
        calls = 0

        def mutate_before_second_verify(bundle: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                environment_path = bundle / "environment.json"
                environment = json.loads(
                    environment_path.read_text(encoding="utf-8")
                )
                environment["platform"] = "Linux-mutated-x86_64"
                write_json(environment_path, environment)
                self.fixture.reseal()
            original_verify(bundle)

        with mock.patch.object(
            EXPORTER,
            "verify_restricted_bundle",
            side_effect=mutate_before_second_verify,
        ):
            with self.assertRaisesRegex(
                EXPORTER.ExportError, "seal changed during export"
            ):
                EXPORTER.export_public(
                    bundle=self.fixture.bundle,
                    output=self.fixture.output,
                    alias_map=self.fixture.alias_map,
                    panel_description=(
                        "Neutral single-end demonstration panel"
                    ),
                    denylist_path=self.fixture.denylist,
                    private_export_receipt=self.fixture.private_receipt,
                    evidence_set_id=EVIDENCE_SET_ID,
                )
        self.assertFalse(self.fixture.output.exists())
        self.assertFalse(self.fixture.private_receipt.exists())

    def test_private_review_input_mutation_is_detected(self) -> None:
        original_verify = EXPORTER.verify_restricted_bundle
        calls = 0

        def mutate_alias_before_second_verify(bundle: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                self.fixture.write_aliases(
                    {SOURCE_ID: "panel-se-02"}
                )
            original_verify(bundle)

        with mock.patch.object(
            EXPORTER,
            "verify_restricted_bundle",
            side_effect=mutate_alias_before_second_verify,
        ):
            with self.assertRaisesRegex(
                EXPORTER.ExportError,
                "private export input changed during export",
            ):
                EXPORTER.export_public(
                    bundle=self.fixture.bundle,
                    output=self.fixture.output,
                    alias_map=self.fixture.alias_map,
                    panel_description=(
                        "Neutral single-end demonstration panel"
                    ),
                    denylist_path=self.fixture.denylist,
                    private_export_receipt=self.fixture.private_receipt,
                    evidence_set_id=EVIDENCE_SET_ID,
                )
        self.assertFalse(self.fixture.output.exists())
        self.assertFalse(self.fixture.private_receipt.exists())

    def test_independent_stage_orders_are_accepted(self) -> None:
        rows = read_tsv(self.fixture.bundle / "design.tsv")
        raw = {
            (row["implementation"], row["mode"]): row["order"]
            for row in rows
            if row["stage"] == "raw"
        }
        ready = {
            (row["implementation"], row["mode"]): row["order"]
            for row in rows
            if row["stage"] == "end_to_end_ready"
        }
        self.assertNotEqual(raw, ready)
        completed = self.fixture.run()
        self.assertEqual(completed.returncode, 0, completed.stderr)

        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            fixture = RestrictedBundleFixture(Path(directory))
            for filename, fields in (
                ("design.tsv", EXPORTER.DESIGN_INPUT_FIELDS),
                ("measurements.tsv", EXPORTER.MEASUREMENT_INPUT_FIELDS),
            ):
                path = fixture.bundle / filename
                tampered = read_tsv(path)
                ready_rows = [
                    row
                    for row in tampered
                    if row["stage"] == "end_to_end_ready"
                ]
                ready_rows[0]["order"], ready_rows[1]["order"] = (
                    ready_rows[1]["order"],
                    ready_rows[0]["order"],
                )
                write_tsv(path, fields, tampered)
            fixture.reseal()
            completed = fixture.run()
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("schedule order", completed.stderr)

    def test_exporter_input_schemas_track_runner_and_summarizer(self) -> None:
        runner = load_module(
            "dumi_public_export_runner_contract",
            ROOT / "scripts" / "benchmark" / "run_benchmark.py",
        )
        summarizer = load_module(
            "dumi_public_export_summary_contract",
            ROOT / "scripts" / "benchmark" / "summarize_results.py",
        )
        self.assertEqual(
            tuple(EXPORTER.MEASUREMENT_INPUT_FIELDS),
            tuple(runner.MEASUREMENT_COLUMNS),
        )
        self.assertEqual(
            tuple(EXPORTER.DESIGN_INPUT_FIELDS),
            tuple(summarizer.DESIGN_FIELDS),
        )
        self.assertEqual(
            tuple(EXPORTER.SUMMARY_INPUT_FIELDS),
            tuple(summarizer.SUMMARY_FIELDS),
        )
        self.assertEqual(
            tuple(EXPORTER.COMPARISON_INPUT_FIELDS),
            tuple(summarizer.COMPARISON_FIELDS),
        )
        self.assertEqual(
            tuple(EXPORTER.CORRECTNESS_INPUT_FIELDS),
            tuple(summarizer.CORRECTNESS_FIELDS),
        )


if __name__ == "__main__":
    unittest.main()
