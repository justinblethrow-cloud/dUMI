#!/usr/bin/env python3
"""Export a privacy-safe public projection of restricted external evidence.

The restricted bundle remains the authoritative validation record.  This
exporter verifies that bundle, then writes a deliberately smaller public view
that contains performance measurements and correctness attestations without
private paths, record counts, byte counts, or data-derived fingerprints.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import ctypes
import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import errno
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from typing import Iterable, Mapping, Sequence
import unicodedata


PUBLIC_FILES = (
    "README.md",
    "manifest.public.json",
    "environment.public.json",
    "design.public.tsv",
    "measurements.public.tsv",
    "summary.public.tsv",
    "comparisons.public.tsv",
    "correctness.public.tsv",
    "SHA256SUMS",
)
CHECKSUMMED_PUBLIC_FILES = PUBLIC_FILES[:-1]
RESTRICTED_ROOT_FILES = (
    "STATUS.json",
    "MANIFEST.sha256",
    "evidence.sha256",
    "privacy-scan.json",
    "external-log-redaction.json",
    "manifest.json",
    "environment.json",
    "environment.txt",
    "design.tsv",
    "measurements.tsv",
    "summary.tsv",
    "comparisons.tsv",
    "correctness.tsv",
)

STATUS_FIELDS = {"detail", "state", "updated_at_utc"}
MANIFEST_FIELDS = {
    "automatic_publication",
    "builds",
    "canonical",
    "cluster_tag_jvm_options",
    "config",
    "contains_source_content_hashes",
    "created_at_utc",
    "dependencies",
    "dependency_files",
    "dumi",
    "external_inputs",
    "external_provenance_ledger",
    "format",
    "harness_commit_binding",
    "harness_files",
    "implementation_sources",
    "intermediate",
    "jvm_options",
    "publication_profile",
    "runtime_id",
    "subprocess_environment",
    "timing_design_version",
    "workloads",
}
CONFIG_FIELDS = {
    "active_processors",
    "allow_output_in_repo",
    "cluster_tag_xmx",
    "cluster_tag_xmx_source",
    "cluster_sort_command",
    "dumi_ref",
    "dumi_source_sha",
    "external_workload_ids",
    "hotspot_families",
    "include_intermediate",
    "input_mode",
    "keep_outputs",
    "moderate_families_per_group",
    "moderate_groups",
    "paired_pairs_per_reference",
    "paired_references",
    "profile",
    "repetitions",
    "seed",
    "selected_workloads",
    "sparse_records",
    "timing_design_version",
    "xms",
    "xmx",
}
CANONICAL_FIELDS = {"provenance_ref", "sha", "url"}
DUMI_FIELDS = {
    "ref",
    "ref_recorded",
    "sha",
    "uncommitted_worktree_sources_excluded",
    "url",
    "worktree_was_dirty",
}
DEPENDENCY_FIELDS = {"filename", "sha256", "url"}
DEPENDENCY_FILE_FIELDS = {"path", "sha256"}
WORKLOAD_FIELDS = {
    "directional_oracle_gate",
    "forced_on_contract_recorded",
    "generator_arguments",
    "input_mode",
    "name",
    "paired",
    "pairwise_cluster_diagnostic",
    "performance_comparability",
    "rationale_provided",
    "scale",
    "streaming_on_eligible",
    "timing_stage_schedule",
    "umi_length",
    "umi_separator",
}
TIMING_STAGE_SCHEDULE_FIELDS = {
    "capacity_available_bytes",
    "capacity_directional_oracle_peak_stage_bytes",
    "capacity_receipt",
    "capacity_required_available_bytes",
    "capacity_status",
    "capacity_timing_peak_stage_bytes",
    "complete_order_cycles",
    "cross_stage_order_matching_required",
    "end_to_end_ready_cells",
    "end_to_end_ready_order",
    "end_to_end_ready_order_offset",
    "execution_order",
    "fresh_deduplication_per_stage_cell",
    "order_family",
    "publication_grade_external_schedule",
    "raw_cells",
    "raw_order_offset",
    "repetitions",
    "scope",
    "timing_design_version",
    "treatments",
    "validation_and_deletion",
}
CAPACITY_RECEIPT_FIELDS = {
    "available_bytes",
    "directional_oracle_active_persistent_bytes",
    "directional_oracle_alignment_umi_aggregate_bytes_each",
    "directional_oracle_alignment_key_bytes_per_record",
    "directional_oracle_applicable",
    "directional_oracle_concurrent_sort_buffer_memory_bytes",
    "directional_oracle_concurrent_sort_destination_merge_bytes",
    "directional_oracle_membership_canonical_bytes_each",
    "directional_oracle_peak_stage_bytes",
    "directional_oracle_record_count_upper_bound",
    "directional_oracle_retained_canonical_bytes",
    "directional_oracle_rooted_canonical_bytes_each",
    "directional_oracle_sort_destination_merge_bytes",
    "directional_oracle_source_record_key_bytes",
    "directional_oracle_tagged_bam_allowance_bytes",
    "directional_oracle_tagged_bam_bytes_each",
    "directional_oracle_tagged_record_key_bytes_each",
    "estimated_output_bytes_per_cell",
    "headroom_bytes",
    "input_bam_bytes",
    "peak_stage_output_bytes",
    "required_available_bytes",
    "retained_block_output_allowances",
    "samtools_sort_scratch_allowances",
    "scope",
    "status",
    "timing_peak_stage_bytes",
    "treatments_per_repetition_block",
}
EXTERNAL_INPUT_FIELDS = {
    "alias_neutrality_machine_verified",
    "bytes",
    "declared_sort_order",
    "forced_on_contract",
    "mapped_records",
    "paired",
    "paired_index",
    "paired_records",
    "path_recorded",
    "private_timing_snapshot",
    "provenance_ledger",
    "qnames_checked",
    "quickcheck_status",
    "rationale_provided",
    "reference_dictionary_sha256",
    "reference_sequences",
    "sha256",
    "temporary_index_validation",
    "total_records",
    "umi_length",
    "umi_separator",
    "workload_id",
}
FORCED_ON_FIELDS = {
    "eligible",
    "exit_code",
    "fallback_marker_seen",
    "logs_suppressed",
    "observed_route",
    "observed_sort_order",
    "output_created",
    "rejection_reason",
    "status",
    "streaming_marker_seen",
    "timed_cell_scheduled",
}
PRIVATE_SNAPSHOT_FIELDS = {
    "bytes",
    "kind",
    "paired_index",
    "path_recorded",
    "read_only",
    "retained_after_sealing",
    "sha256",
    "timing_index",
}
PAIRED_INDEX_FIELDS = {"bytes", "path_recorded", "sha256", "validation"}
PRIVATE_PAIRED_INDEX_FIELDS = {
    "bytes",
    "format",
    "path_recorded",
    "sha256",
}
PAIRWISE_RECEIPT_FIELDS = {
    "configuration",
    "equivalent",
    "left",
    "partition_equivalent",
    "partition_fingerprint_version",
    "read_group_dictionary_equivalent",
    "reference_dictionary_equivalent",
    "right",
    "schema",
    "temporary_storage",
}
PAIRWISE_GATE_FIELDS = {
    "applicable",
    "equivalent",
    "input",
    "input_sha256",
    "left_implementation",
    "partition_equivalent",
    "partition_fingerprint_version",
    "private_partition_streams_retained",
    "read_group_dictionary_equivalent",
    "receipt",
    "receipt_sha256",
    "reference_dictionary_equivalent",
    "right_implementation",
    "status",
    "tagged_outputs_retained",
    "untimed",
}
PAIRWISE_CONFIGURATION_FIELDS = {
    "mode",
    "remove_chimeric",
    "remove_unpaired",
    "sort_buffer_size",
    "umi_length",
    "umi_separator_bytes",
    "umi_separator_sha256",
}
PAIRWISE_SIDE_COUNT_FIELDS = {
    "alignment_groups",
    "canonical_partition_bytes",
    "clusters",
    "eligible_records",
    "excluded_chimeric",
    "excluded_mate_unmapped",
    "excluded_second_of_pair",
    "excluded_unmapped",
    "excluded_unpaired",
    "input_records",
    "max_umi_memberships_per_cluster",
    "read_groups",
    "record_key_bytes",
    "reference_sequences",
    "umi_memberships",
}
PAIRWISE_SIDE_HASH_FIELDS = {
    "partition_cluster_multiset_sha256",
    "read_group_dictionary_sha256",
    "reference_dictionary_sha256",
}
PAIRWISE_TEMPORARY_STORAGE_FIELDS = {
    "persistent_stage_peak_upper_bound_bytes",
    "sort_merge_storage_note",
}
DIRECTIONAL_ORACLE_SCHEMA = "dumi-directional-oracle-check-v1"
DIRECTIONAL_ORACLE_SCHEMA_VERSION = 1
DIRECTIONAL_ORACLE_METHODS = {
    "membership_oracle": "string-hamming-directional-v1",
    "root_total_order": "dumi-bitset-signed-chunks-v1",
    "threshold": "java-binary32-directional-threshold-v1",
    "membership_partition": "alignment-cluster-umi-frequency-v1",
    "rooted_partition": "alignment-root-umi-frequency-v1",
}
DIRECTIONAL_PUBLIC_INDEPENDENT_COMPONENTS = (
    "directional-clustering",
    "distance-evaluation",
    "threshold-evaluation",
    "root-construction",
)
DIRECTIONAL_PUBLIC_SHARED_TRANSPORT_COMPONENTS = (
    "SAM-parsing",
    "QNAME-UMI-extraction",
    "alignment-grouping",
    "header-parsing",
    "external-sorting",
)
DIRECTIONAL_GATE_FIELDS = {
    "directional_oracle_gate_pass",
    "dumi_off_oracle_partition_equivalent",
    "dumi_off_oracle_root_assignment_equivalent",
    "dumi_off_source_reference_dictionary_equivalent",
    "dumi_off_source_read_group_dictionary_equivalent",
}
DIRECTIONAL_DIAGNOSTIC_FIELDS = {
    "canonical_upstream_oracle_partition_equivalent",
    "canonical_upstream_oracle_root_assignment_equivalent",
    "canonical_upstream_dumi_off_partition_equivalent",
    "canonical_upstream_dumi_off_root_assignment_equivalent",
    "canonical_upstream_source_reference_dictionary_equivalent",
    "canonical_upstream_source_read_group_dictionary_equivalent",
}
DIRECTIONAL_METRIC_COUNT_FIELDS = {
    "alignment_umi_frequency_multiset_bytes",
    "input_bytes",
    "records",
    "alignment_groups",
    "clusters",
    "umi_memberships",
    "max_umi_memberships_per_cluster",
    "membership_partition_bytes",
    "rooted_partition_bytes",
    "input_records",
    "eligible_records",
    "excluded_unmapped",
    "excluded_second_of_pair",
    "excluded_unpaired",
    "excluded_mate_unmapped",
    "excluded_chimeric",
    "record_key_bytes",
    "reference_sequences",
    "read_groups",
}
DIRECTIONAL_METRIC_HASH_FIELDS = {
    "alignment_umi_frequency_multiset_sha256",
    "input_sha256",
    "membership_partition_sha256",
    "rooted_partition_sha256",
    "reference_dictionary_sha256",
    "read_group_dictionary_sha256",
}
DIRECTIONAL_RECEIPT_FIELDS = {
    "canonical_upstream",
    "configuration",
    "diagnostics",
    "dumi_off",
    "gate",
    "methods",
    "provenance",
    "schema",
    "source_oracle",
    "temporary_storage",
    "version",
}
DIRECTIONAL_CONFIGURATION_FIELDS = {
    "edit_distance",
    "mode",
    "percentage_binary32_hex",
    "percentage_decimal",
    "remove_chimeric",
    "remove_unpaired",
    "sort_buffer_size",
    "umi_length",
    "umi_separator_bytes",
    "umi_separator_sha256",
}
DIRECTIONAL_PROVENANCE_FIELDS = {
    "helper_sha256",
    "partition_checker_sha256",
    "private_streams_retained",
}
DIRECTIONAL_TEMPORARY_STORAGE_FIELDS = {
    "persistent_stage_peak_upper_bound_bytes",
    "sort_merge_storage_note",
}
DIRECTIONAL_MANIFEST_GATE_FIELDS = {
    "applicable",
    "diagnostics",
    "directional_oracle_gate_pass",
    "dumi_off_oracle_partition_equivalent",
    "dumi_off_oracle_root_assignment_equivalent",
    "dumi_off_source_read_group_dictionary_equivalent",
    "dumi_off_source_reference_dictionary_equivalent",
    "input",
    "input_sha256",
    "methods",
    "post_timing_capacity_available_bytes",
    "post_timing_capacity_receipt",
    "post_timing_capacity_required_available_bytes",
    "private_oracle_streams_retained",
    "receipt",
    "receipt_sha256",
    "status",
    "tagged_outputs_retained",
    "untimed",
}
PAIRWISE_MANIFEST_DIAGNOSTIC_FIELDS = {
    "applicable",
    "equivalent",
    "partition_equivalent",
    "private_partition_streams_retained",
    "read_group_dictionary_equivalent",
    "receipt",
    "receipt_sha256",
    "reference_dictionary_equivalent",
    "scope",
    "status",
    "tagged_outputs_retained",
    "untimed",
}
PERFORMANCE_COMPARABILITY_FIELDS = {
    "applicable",
    "cross_implementation_alignment_group_output_count_match",
    "cross_implementation_exact_match",
    "cross_implementation_output_count_match",
    "issues",
    "status",
}
NONCOMPARABLE_OUTPUT_COUNT_ISSUE = (
    "cross-implementation-output-count-mismatch"
)
HARNESS_PATHS = (
    "harness/run_benchmark.py",
    "harness/generate_workload.py",
    "harness/semantic_check.py",
    "harness/cluster_partition_check.py",
    "harness/directional_oracle_check.py",
    "harness/summarize_results.py",
    "harness/README.md",
)
HARNESS_FILE_FIELDS = {"path", "sha256"}
HARNESS_COMMIT_BINDING_FIELDS = {
    "commit_sha",
    "files",
    "repository_url",
    "status",
}
HARNESS_COMMIT_FILE_FIELDS = {
    "repository_path",
    "sha256",
    "snapshot_path",
}
HARNESS_REPOSITORY_PATHS = tuple(
    f"scripts/benchmark/{PurePosixPath(path).name}"
    for path in HARNESS_PATHS
)
BUILD_FIELDS = {
    "classes_tree_sha256",
    "command_file",
    "label",
    "source_count",
    "source_tree_sha256",
}
INPUT_HASH_RECEIPT_FIELDS = {
    "bam",
    "cross_implementation_diagnostic",
    "input_mode",
    "oracles",
    "paired",
    "rationale_provided",
    "umi_length",
    "umi_separator",
    "validation",
    "workload_id",
}
EXTERNAL_PROVENANCE_LEDGER_FIELDS = {
    "schema",
    "version",
    "sha256",
    "workload_count",
    "authorization_confirmed",
    "pre_deduplication_confirmed",
    "path_recorded",
    "content_retained",
}
EXTERNAL_PROVENANCE_LEDGER_SCHEMA = "dumi-external-provenance-ledger"
EXTERNAL_PROVENANCE_LEDGER_VERSION = 1
INPUT_HASH_BAM_FIELDS = {"bytes", "path_recorded", "sha256"}
INPUT_HASH_VALIDATION_FIELDS = {
    "declared_sort_order",
    "quickcheck_status",
    "temporary_index_validation",
}
ORACLE_IDENTITY_FIELDS = {
    "implementation",
    "kind",
    "output_records",
    "output_retained",
    "reference_dictionary_sha256",
    "reference_sequences",
    "semantic_sha256",
    "source_sha",
    "timed",
}
CROSS_IMPLEMENTATION_RECEIPT_FIELDS = {
    "alignment_group_output_count_match",
    "alignment_group_output_count_multiset_equal",
    "alignment_group_output_record_counts_equal",
    "exact_match",
    "output_count_match",
    "excluded_second_of_pair_counts_equal",
    "excluded_unmapped_counts_equal",
    "ordered_rg_equal",
    "ordered_sq_equal",
    "record_counts_equal",
    "scope",
    "status",
}
SEMANTIC_INSPECTION_FIELDS = {
    "alignment_group_fingerprint_version",
    "alignment_group_mode",
    "alignment_group_output_count_equivalent",
    "alignment_group_output_count_reused_from_exact_reference",
    "alignment_group_output_count_sha256",
    "alignment_group_output_records",
    "alignment_group_records_excluded_second_of_pair",
    "alignment_group_records_excluded_unmapped",
    "exact_oracle_match",
    "expected_read_group_dictionary_sha256",
    "expected_read_groups",
    "expected_reference_dictionary_sha256",
    "expected_reference_sequences",
    "output_bytes",
    "output_file",
    "output_records",
    "output_sha256",
    "quickcheck",
    "quickcheck_status",
    "read_group_dictionary_equivalent",
    "read_group_dictionary_sha256",
    "read_groups",
    "record_equivalent",
    "reference_alignment_group_output_count_sha256",
    "reference_alignment_group_output_records",
    "reference_alignment_group_records_excluded_second_of_pair",
    "reference_alignment_group_records_excluded_unmapped",
    "reference_cache_receipt_sha256",
    "reference_cache_receipt_verified",
    "reference_canonical_sha256",
    "reference_canonical_sha256_verified",
    "reference_dictionary_equivalent",
    "reference_dictionary_sha256",
    "reference_file",
    "reference_file_sha256",
    "reference_sequences",
    "semantic_sha256",
    "sort_order",
}
TIMED_INSPECTION_FIELDS = SEMANTIC_INSPECTION_FIELDS | {"actual_route"}
ALIGNMENT_GROUP_FINGERPRINT_VERSION = (
    "dumi-umicollapse-alignment-group-output-count-v1"
)
REDACTED_LOG_CONTENT = (
    "External-input log content suppressed after validation or failure; "
    "commands, timings, and semantic receipts are retained.\n"
)
ENVIRONMENT_ALLOWED_FIELDS = {
    "captured_at_utc",
    "cpu_affinity",
    "cpu_scaling_governor",
    "environment_policy",
    "git",
    "gnu_sort",
    "gnu_time",
    "java",
    "javac",
    "load_average_1m_5m_15m",
    "logical_cpu_count",
    "lscpu",
    "network_environment_variable_names",
    "platform",
    "python",
    "removed_injection_environment_variables",
    "samtools",
    "subprocess_environment",
    "uname",
}
ENVIRONMENT_REQUIRED_FIELDS = {
    "environment_policy",
    "git",
    "gnu_sort",
    "gnu_time",
    "java",
    "javac",
    "logical_cpu_count",
    "platform",
    "python",
    "samtools",
}
SUBPROCESS_ENVIRONMENT_FIELDS = {
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "TMPDIR",
    "TZ",
}
INJECTION_ENVIRONMENT_VARIABLES = {
    "CLASSPATH",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
    "JDK_JAVA_OPTIONS",
    "JAVA_TOOL_OPTIONS",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "_JAVA_OPTIONS",
}
NETWORK_ENVIRONMENT_VARIABLES = {
    "ALL_PROXY",
    "CURL_CA_BUNDLE",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "all_proxy",
    "https_proxy",
    "http_proxy",
    "no_proxy",
}

DESIGN_INPUT_FIELDS = (
    "run_id",
    "workload",
    "scale",
    "stage",
    "implementation",
    "mode",
    "repetition",
    "order",
)
MEASUREMENT_INPUT_FIELDS = (
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
    "command_file",
    "stdout_file",
    "stderr_file",
    "output_file",
)
METRICS = ("elapsed_s", "user_s", "system_s", "cpu_pct", "max_rss_kib")
SUMMARY_INPUT_FIELDS = (
    "workload",
    "scale",
    "stage",
    "implementation",
    "mode",
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
    *(
        f"{metric}_{statistic}"
        for metric in METRICS
        for statistic in ("n", "median", "min", "max", "range", "mad")
    ),
)
COMPARISON_METRICS = (
    "elapsed_speedup",
    "elapsed_change_pct",
    "max_rss_reduction_pct",
)
COMPARISON_INPUT_FIELDS = (
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
CORRECTNESS_INPUT_FIELDS = (
    "workload",
    "scale",
    "stage",
    "implementation",
    "mode",
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

DESIGN_PUBLIC_FIELDS = (
    "run_id",
    "workload_id",
    "stage",
    "implementation",
    "mode",
    "repetition",
    "order",
)
MEASUREMENT_PUBLIC_FIELDS = (
    *DESIGN_PUBLIC_FIELDS,
    "exit_code",
    "elapsed_s",
    "user_s",
    "system_s",
    "cpu_pct",
    "max_rss_kib",
    "actual_route",
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
)
SUMMARY_PUBLIC_FIELDS = (
    "workload_id",
    "stage",
    "implementation",
    "mode",
    "attempts",
    "successful_repetitions",
    "failed_repetitions",
    "correctness_status",
    "comparability_status",
    "comparability_issues",
    *(
        f"{metric}_{statistic}"
        for metric in METRICS
        for statistic in ("n", "median", "min", "max", "range", "mad")
    ),
)
COMPARISON_PUBLIC_FIELDS = (
    "workload_id",
    "stage",
    "baseline_implementation",
    "baseline_mode",
    "implementation",
    "mode",
    "attempted_pairs",
    "successful_pairs",
    "failed_pairs",
    "noncomparable_pairs",
    "correctness_status",
    "comparability_status",
    "comparability_issues",
    "cross_implementation_exact_match",
    "cross_implementation_bounded_diagnostic_match",
    *(
        f"{metric}_{statistic}"
        for metric in COMPARISON_METRICS
        for statistic in ("n", "median", "min", "max", "range", "mad")
    ),
)
CORRECTNESS_PUBLIC_FIELDS = (
    "workload_id",
    "stage",
    "implementation",
    "mode",
    "correctness_status",
    "cross_implementation_exact_match",
    "cross_implementation_output_count_match",
    "cross_implementation_alignment_group_output_count_match",
    "cross_implementation_bounded_diagnostic_match",
    "directional_oracle_gate_pass",
    "dumi_off_oracle_partition_equivalent",
    "dumi_off_oracle_root_assignment_equivalent",
    "dumi_off_source_reference_dictionary_equivalent",
    "dumi_off_source_read_group_dictionary_equivalent",
    "canonical_upstream_oracle_partition_equivalent",
    "canonical_upstream_oracle_root_assignment_equivalent",
    "canonical_upstream_dumi_off_partition_equivalent",
    "canonical_upstream_dumi_off_root_assignment_equivalent",
    "canonical_upstream_source_reference_dictionary_equivalent",
    "canonical_upstream_source_read_group_dictionary_equivalent",
    "upstream_agreement_required",
    "pairwise_cluster_diagnostic_equivalent",
    "pairwise_cluster_partition_equivalent",
    "pairwise_reference_dictionary_equivalent",
    "pairwise_read_group_dictionary_equivalent",
    "issue_count",
)

SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
PUBLIC_ALIAS = re.compile(r"^(?:demo|panel)-(?:se|pe)-[0-9]{2,4}$")
PRIVATE_SOURCE_ID = re.compile(
    r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$"
)
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*\.jar$")
SAFE_IMPLEMENTATIONS = {"canonical-upstream", "dumi"}
SAFE_MODES = {"legacy", "off", "on", "auto"}
SAFE_STAGES = {"raw", "end_to_end_ready"}
SAFE_COMPARISON_STAGES = SAFE_STAGES
SAFE_ROUTES = {
    "coordinate",
    "fallback-off",
    "off",
    "off-ineligible",
    "streaming",
}
PRIVATE_PATH_PATTERNS = (
    re.compile(r"""(?<![A-Za-z0-9/])/(?!/)[^\s"'`<>]+"""),
    re.compile(r"(?i)(?:file|gs|s3)://"),
    re.compile(r"(?i)[A-Z]:\\"),
    re.compile(r"\\\\[^\\\s]+\\[^\\\s]+"),
)
HEX_RUN_IN_TEXT = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{64,}(?![0-9A-Fa-f])")
URI_IN_TEXT = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>]+")
PANEL_DESCRIPTION = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9 .,:;()_+'%/-]{0,499}$"
)
EVIDENCE_FILES = (
    "design.tsv",
    "manifest.json",
    "environment.json",
    "environment.txt",
    "measurements.tsv",
    "summary.tsv",
    "correctness.tsv",
    "comparisons.tsv",
)
CANONICAL_PUBLIC_URL = (
    "https://github.com/Daniel-Liu-c0deb0t/UMICollapse.git"
)
CANONICAL_SOURCE_SHA = "efeab35f5d29dec1d496ade3f681eeb34d9c2057"
CANONICAL_SOURCE_TREE_SHA256 = (
    "be4b58efec066208c68955abd099094ad018fae5c41efb9b0e6f75dc2749d8c6"
)
DUMI_PUBLIC_URL = "https://github.com/justinblethrow-cloud/dUMI.git"
SINGLE_END_FORCED_ON_REJECTION_REASONS = {
    "record-order",
    "reverse-coordinate-overflow",
    "positive-lag-window",
}
LOCKED_DEPENDENCIES = (
    (
        "htsjdk-3.0.5.jar",
        "8d03dc7672199f10fe4bad8aaf76259e36d15ed8fb145d6427ef1efb51a4da5f",
        "https://repo.maven.apache.org/maven2/com/github/samtools/"
        "htsjdk/3.0.5/htsjdk-3.0.5.jar",
    ),
    (
        "snappy-java-1.1.10.8.jar",
        "50485d06037fea3d6e40c968386feeca6338cc9872e25549593ff3eb352cefcc",
        "https://repo.maven.apache.org/maven2/org/xerial/snappy/"
        "snappy-java/1.1.10.8/snappy-java-1.1.10.8.jar",
    ),
)
DEPENDENCIES_LOCK_SHA256 = (
    "fc6e03716934dce220a5e7f1fbfaaf7c838c1c183da50ef8286e70708578e8aa"
)
PUBLIC_DEPENDENCY_URLS = {
    filename: url for filename, _digest, url in LOCKED_DEPENDENCIES
}
PUBLIC_DEPENDENCY_SHA256 = {
    filename: digest for filename, digest, _url in LOCKED_DEPENDENCIES
}
PUBLIC_URIS = {
    CANONICAL_PUBLIC_URL,
    DUMI_PUBLIC_URL,
    *PUBLIC_DEPENDENCY_URLS.values(),
}
SAFE_UMI_SEPARATOR = re.compile(r"^[._:+-]{1,8}$")
SAFE_MEMORY_SIZE = re.compile(r"^[1-9][0-9]*[kKmMgG]$")
EVIDENCE_SET_ID = re.compile(
    r"^external-evidence-[a-z0-9]+(?:-[a-z0-9]+)*$"
)
BUILTIN_FORBIDDEN_TOKENS = (
    bytes.fromhex("506c61736d6964736175727573").decode("ascii"),
)


class ExportError(RuntimeError):
    """The restricted evidence cannot be exported safely."""


@dataclass(frozen=True)
class PrivateDenylist:
    tokens: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    hashes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PathIdentity:
    device: int
    inode: int
    expected_file_type: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_private_permissions(path: Path, context: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ExportError(f"could not inspect {context} permissions") from error
    if path.is_symlink():
        raise ExportError(f"{context} must not be a symbolic link")
    if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
        raise ExportError(f"{context} must be owned by the current user")
    if metadata.st_mode & 0o077:
        raise ExportError(f"{context} must not grant group or other access")


def require_current_owner(path: Path, context: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ExportError(f"could not inspect {context} ownership") from error
    if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
        raise ExportError(f"{context} must be owned by the current user")


def strict_json(path: Path, context: str) -> object:
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise ExportError(f"could not read {context}") from error
    if b"\x00" in payload:
        raise ExportError(f"{context} contains NUL bytes")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ExportError(f"{context} is not UTF-8 text") from error
    try:
        def reject_duplicate_keys(
            pairs: list[tuple[str, object]],
        ) -> dict[str, object]:
            output: dict[str, object] = {}
            for key, value in pairs:
                if key in output:
                    raise ExportError(f"{context} contains a duplicate key")
                output[key] = value
            return output

        def reject_nonfinite(value: str) -> object:
            raise ExportError(f"{context} contains a non-finite number")

        return json.loads(
            text,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonfinite,
        )
    except ExportError:
        raise
    except json.JSONDecodeError as error:
        raise ExportError(f"{context} is not valid JSON") from error


def require_object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise ExportError(f"{context} must be a JSON object")
    return value


def require_unique_external_input_hashes(
    receipts: Sequence[object],
) -> None:
    seen: set[str] = set()
    for value in receipts:
        receipt = require_object(value, "external-input receipt")
        digest = receipt.get("sha256")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise ExportError("restricted external-input receipt has an invalid hash")
        if digest in seen:
            raise ExportError(
                "restricted external-input receipts reuse BAM content"
            )
        seen.add(digest)


def require_exact_fields(
    value: Mapping[str, object], expected: set[str], context: str
) -> None:
    if set(value) != expected:
        raise ExportError(f"{context} does not match the expected schema")


def require_allowed_fields(
    value: Mapping[str, object],
    allowed: set[str],
    required: set[str],
    context: str,
) -> None:
    if not required.issubset(value) or not set(value).issubset(allowed):
        raise ExportError(f"{context} does not match the expected schema")


def positive_integer(value: object, context: str) -> int:
    if isinstance(value, bool):
        raise ExportError(f"{context} must be a positive integer")
    try:
        parsed = int(str(value))
    except ValueError as error:
        raise ExportError(f"{context} must be a positive integer") from error
    if parsed <= 0:
        raise ExportError(f"{context} must be a positive integer")
    return parsed


def nonnegative_integer(value: object, context: str) -> int:
    if isinstance(value, bool):
        raise ExportError(f"{context} must be a nonnegative integer")
    try:
        parsed = int(str(value))
    except ValueError as error:
        raise ExportError(f"{context} must be a nonnegative integer") from error
    if parsed < 0:
        raise ExportError(f"{context} must be a nonnegative integer")
    return parsed


def finite_decimal(
    value: object,
    context: str,
    *,
    percent: bool = False,
    nonnegative: bool = True,
) -> str:
    text = str(value).strip()
    if percent and text.endswith("%"):
        text = text[:-1]
    if len(text) > 100:
        raise ExportError(f"{context} must be a bounded finite decimal")
    try:
        parsed = Decimal(text)
    except InvalidOperation as error:
        raise ExportError(f"{context} must be a finite decimal") from error
    if (
        not parsed.is_finite()
        or (parsed and abs(parsed.adjusted()) > 1000)
        or (nonnegative and parsed < 0)
    ):
        qualifier = "nonnegative " if nonnegative else ""
        raise ExportError(f"{context} must be a {qualifier}finite decimal")
    return format_decimal(parsed)


def decimal_number(
    value: object,
    context: str,
    *,
    percent: bool = False,
    nonnegative: bool = True,
) -> Decimal:
    text = str(value).strip()
    if percent and text.endswith("%"):
        text = text[:-1]
    if len(text) > 100:
        raise ExportError(f"{context} must be a bounded finite decimal")
    try:
        number = Decimal(text)
    except InvalidOperation as error:
        raise ExportError(f"{context} must be a finite decimal") from error
    if (
        not number.is_finite()
        or (number and abs(number.adjusted()) > 1000)
        or (nonnegative and number < 0)
    ):
        qualifier = "nonnegative " if nonnegative else ""
        raise ExportError(f"{context} must be a {qualifier}finite decimal")
    return number


def format_decimal(number: Decimal) -> str:
    if not number.is_finite():
        raise ExportError("cannot format a non-finite decimal")
    if number == 0:
        return "0"
    if number == number.to_integral():
        return str(number.quantize(Decimal(1)))
    return format(number.normalize(), "f")


def decimal_median(numbers: list[Decimal]) -> Decimal:
    if not numbers:
        raise ExportError("cannot summarize an empty metric")
    ordered = sorted(numbers)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def decimal_statistics(numbers: list[Decimal]) -> dict[str, Decimal]:
    center = decimal_median(numbers)
    minimum = min(numbers)
    maximum = max(numbers)
    return {
        "median": center,
        "min": minimum,
        "max": maximum,
        "range": maximum - minimum,
        "mad": decimal_median([abs(number - center) for number in numbers]),
    }


def strict_boolean(value: object, context: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value == "True":
            return True
        if value == "False":
            return False
    raise ExportError(f"{context} must be True or False")


def safe_one_line(value: object, context: str, *, maximum: int = 1000) -> str:
    if not isinstance(value, str):
        raise ExportError(f"{context} must be text")
    text = value.strip()
    if (
        not text
        or len(text) > maximum
        or any(character in text for character in "\r\n\t\x00")
    ):
        raise ExportError(f"{context} must be bounded one-line text")
    return text


def first_version_line(value: object, context: str) -> str:
    if not isinstance(value, str):
        raise ExportError(f"{context} must be text")
    for line in value.splitlines():
        if line.strip():
            return safe_one_line(line, context, maximum=300)
    raise ExportError(f"{context} contains no version line")


def is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def safe_relative_path(value: str, context: str) -> PurePosixPath:
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or "\\" in value
    ):
        raise ExportError(f"{context} is not a safe relative path")
    return relative


def verify_restricted_bundle(bundle: Path) -> None:
    if not bundle.is_dir() or bundle.is_symlink():
        raise ExportError("restricted bundle must be a real directory")
    require_private_permissions(bundle, "restricted bundle directory")
    for filename in RESTRICTED_ROOT_FILES:
        path = bundle / filename
        if not path.is_file() or path.is_symlink():
            raise ExportError("restricted bundle is missing a required regular file")

    status = require_object(
        strict_json(bundle / "STATUS.json", "restricted STATUS"),
        "restricted STATUS",
    )
    require_exact_fields(status, STATUS_FIELDS, "restricted STATUS")
    if status["state"] != "COMPLETE" or status["detail"] != "":
        raise ExportError("restricted bundle is not in a clean COMPLETE state")

    manifest_path = bundle / "MANIFEST.sha256"
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise ExportError("could not read restricted MANIFEST") from error
    if not lines:
        raise ExportError("restricted MANIFEST is empty")

    expected: dict[str, str] = {}
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([^\r\n]+)", line)
        if match is None:
            raise ExportError("restricted MANIFEST has an invalid entry")
        digest, relative_text = match.groups()
        relative = PurePosixPath(relative_text)
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
            or "\\" in relative_text
            or relative_text in expected
            or relative_text in {"MANIFEST.sha256", "STATUS.json"}
        ):
            raise ExportError("restricted MANIFEST has an unsafe or duplicate path")
        candidate = bundle.joinpath(*relative.parts)
        if (
            not candidate.is_file()
            or candidate.is_symlink()
            or not is_within(candidate, bundle)
        ):
            raise ExportError("restricted MANIFEST names an unsafe or missing file")
        if sha256_file(candidate) != digest:
            raise ExportError("restricted MANIFEST checksum verification failed")
        expected[relative_text] = digest

    actual: set[str] = set()
    for path in bundle.rglob("*"):
        if path.is_symlink():
            raise ExportError("restricted bundle contains a symbolic link")
        if path.is_file():
            require_current_owner(path, "restricted bundle file")
            actual.add(path.relative_to(bundle).as_posix())
        elif path.is_dir():
            require_current_owner(path, "restricted bundle directory")
        else:
            raise ExportError("restricted bundle contains a special file")
    unmanifested_allowed = {"MANIFEST.sha256", "STATUS.json"}
    if actual != set(expected) | unmanifested_allowed:
        raise ExportError("restricted MANIFEST does not exactly inventory the bundle")

    for filename in RESTRICTED_ROOT_FILES[2:]:
        if filename not in expected:
            raise ExportError("restricted MANIFEST omits a required evidence file")

    evidence_path = bundle / "evidence.sha256"
    try:
        evidence_lines = evidence_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise ExportError("could not read restricted evidence receipt") from error
    evidence_entries: dict[str, str] = {}
    for line in evidence_lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._-]+)", line)
        if match is None:
            raise ExportError("restricted evidence receipt has an invalid entry")
        digest, filename = match.groups()
        if filename in evidence_entries:
            raise ExportError("restricted evidence receipt has a duplicate entry")
        evidence_entries[filename] = digest
    if tuple(evidence_entries) != EVIDENCE_FILES:
        raise ExportError("restricted evidence receipt has an unexpected inventory")
    if any(
        sha256_file(bundle / filename) != digest
        for filename, digest in evidence_entries.items()
    ):
        raise ExportError("restricted evidence receipt verification failed")

    privacy_receipt = require_object(
        strict_json(bundle / "privacy-scan.json", "restricted privacy receipt"),
        "restricted privacy receipt",
    )
    require_exact_fields(
        privacy_receipt, {"rules", "status"}, "restricted privacy receipt"
    )
    if privacy_receipt["status"] != "pass" or not isinstance(
        privacy_receipt["rules"], list
    ):
        raise ExportError("restricted privacy gate did not pass")
    log_receipt = require_object(
        strict_json(
            bundle / "external-log-redaction.json",
            "restricted log-redaction receipt",
        ),
        "restricted log-redaction receipt",
    )
    require_exact_fields(
        log_receipt,
        {"policy", "redacted_files", "status"},
        "restricted log-redaction receipt",
    )
    if (
        log_receipt["status"] != "pass"
        or not isinstance(log_receipt["policy"], str)
        or not isinstance(log_receipt["redacted_files"], list)
    ):
        raise ExportError("restricted log-redaction gate did not pass")


def restricted_redacted_paths(bundle: Path) -> set[str]:
    receipt = require_object(
        strict_json(
            bundle / "external-log-redaction.json",
            "restricted log-redaction receipt",
        ),
        "restricted log-redaction receipt",
    )
    values = receipt.get("redacted_files")
    if (
        not isinstance(values, list)
        or not values
        or not all(isinstance(value, str) for value in values)
        or len(values) != len(set(values))
    ):
        raise ExportError("restricted log-redaction inventory is invalid")
    normalized: set[str] = set()
    for value in values:
        path = safe_relative_path(value, "restricted redacted-log path")
        if path.suffix != ".txt" or not re.search(
            r"(?:^|-)(?:stdout|stderr)\.txt$", path.name
        ):
            raise ExportError("restricted log-redaction inventory is invalid")
        normalized.add(path.as_posix())
    return normalized


def load_aliases(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ExportError("private alias map must be a regular file")
    require_private_permissions(path, "private alias map")
    payload = require_object(strict_json(path, "private alias map"), "private alias map")
    require_exact_fields(payload, {"format", "aliases"}, "private alias map")
    if payload["format"] != 1:
        raise ExportError("private alias map format is unsupported")
    aliases = require_object(payload["aliases"], "private alias map aliases")
    normalized: dict[str, str] = {}
    for source, public in aliases.items():
        if not source or any(character in source for character in "\r\n\t\x00"):
            raise ExportError("private alias map contains an invalid source key")
        if not isinstance(public, str) or not PUBLIC_ALIAS.fullmatch(public):
            raise ExportError("private alias map contains an unsafe public alias")
        normalized[source] = public
    if not normalized or len(set(normalized.values())) != len(normalized):
        raise ExportError("private alias map aliases must be nonempty and unique")
    return normalized


def private_input_sha256(path: Path, context: str) -> str:
    if not path.is_file():
        raise ExportError(f"{context} must be a regular file")
    require_private_permissions(path, context)
    try:
        return sha256_file(path)
    except OSError as error:
        raise ExportError(f"could not hash {context}") from error


def load_denylist(path: Path) -> PrivateDenylist:
    if not path.is_file():
        raise ExportError("private denylist must be a regular file")
    require_private_permissions(path, "private denylist")
    payload = require_object(strict_json(path, "private denylist"), "private denylist")
    require_exact_fields(
        payload, {"format", "tokens", "paths", "hashes"}, "private denylist"
    )
    if payload["format"] != 1:
        raise ExportError("private denylist format is unsupported")

    def string_list(name: str, *, minimum: int, maximum: int) -> tuple[str, ...]:
        raw = payload[name]
        if not isinstance(raw, list):
            raise ExportError(f"private denylist {name} must be a list")
        values: list[str] = []
        for item in raw:
            if (
                not isinstance(item, str)
                or len(item) < minimum
                or len(item) > maximum
                or any(character in item for character in "\r\n\t\x00")
            ):
                raise ExportError(f"private denylist {name} contains an invalid value")
            values.append(item)
        if len(values) != len(set(values)):
            raise ExportError(f"private denylist {name} contains a duplicate")
        return tuple(values)

    tokens = string_list("tokens", minimum=3, maximum=200)
    paths = string_list("paths", minimum=3, maximum=4096)
    hashes = string_list("hashes", minimum=64, maximum=64)
    if any(not SHA256.fullmatch(value.lower()) for value in hashes):
        raise ExportError("private denylist hashes must be SHA-256 values")
    return PrivateDenylist(tokens=tokens, paths=paths, hashes=hashes)


def read_tsv(path: Path, fields: tuple[str, ...], context: str) -> list[dict[str, str]]:
    try:
        stream = path.open("r", encoding="utf-8", newline="")
    except OSError as error:
        raise ExportError(f"could not read {context}") from error
    with stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if tuple(reader.fieldnames or ()) != fields:
            raise ExportError(f"{context} does not match the expected schema")
        rows: list[dict[str, str]] = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ExportError(f"{context} has an invalid row")
            if any(
                any(character in value for character in "\r\n\t\x00")
                for value in row.values()
            ):
                raise ExportError(f"{context} has an unsafe field value")
            rows.append(dict(row))
    if not rows:
        raise ExportError(f"{context} contains no rows")
    return rows


def read_small_text(path: Path, context: str, *, maximum: int = 1_000_000) -> str:
    if not path.is_file() or path.is_symlink():
        raise ExportError(f"{context} is missing")
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise ExportError(f"could not read {context}") from error
    if len(payload) > maximum or b"\x00" in payload:
        raise ExportError(f"{context} is not bounded text")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ExportError(f"{context} is not UTF-8 text") from error


def recorded_path(path: Path, bundle: Path, context: str) -> str:
    try:
        return path.relative_to(bundle).as_posix()
    except ValueError as error:
        raise ExportError(f"{context} is outside the restricted bundle") from error


def expected_private_run_root(
    bundle: Path, row: Mapping[str, str]
) -> tuple[Path, str]:
    repetition = positive_integer(row["repetition"], "measurement repetition")
    order = positive_integer(row["order"], "measurement order")
    label = (
        row["implementation"]
        if row["mode"] == "legacy"
        else f"{row['implementation']}-{row['mode']}"
    )
    base_run_id = (
        f"external-{row['scale']}-r{repetition:02d}-o{order:02d}-{label}"
    )
    if row["run_id"] != f"{base_run_id}-{row['stage']}":
        raise ExportError(
            "restricted measurement run ID is not the deterministic runner ID"
        )
    root = bundle / "runs" / base_run_id / row["stage"]
    return root, base_run_id


def runner_sanitized_shlex_join(tokens: Sequence[str]) -> str:
    """Reproduce runner quoting before absolute paths become placeholders."""

    replacements = {
        "<EVIDENCE_DIR>": "/__dumi_evidence__",
        "<JAVA>": "/__dumi_java__",
        "<SAMTOOLS>": "/__dumi_samtools__",
    }
    expanded = []
    for token in tokens:
        value = token
        for placeholder, concrete in replacements.items():
            value = value.replace(placeholder, concrete)
        expanded.append(value)
    joined = shlex.join(expanded)
    for placeholder, concrete in replacements.items():
        joined = joined.replace(concrete, placeholder)
    return joined


def validate_command_receipt(
    command_path: Path,
    *,
    row: Mapping[str, str],
    workload: Mapping[str, object],
    run_root: Path,
) -> None:
    command_text = read_small_text(
        command_path, "restricted per-run command", maximum=64 * 1024
    )
    if not command_text.endswith("\n") or command_text.count("\n") != 1:
        raise ExportError("restricted per-run command is not a single line")
    command_line = command_text[:-1]
    source_key = (
        "upstream"
        if row["implementation"] == "canonical-upstream"
        else "dumi"
    )
    run_prefix = (
        f"<EVIDENCE_DIR>/runs/{run_root.parent.name}/{row['stage']}"
    )
    raw_sort_order = (
        "unsorted" if row["actual_route"] == "streaming" else "coordinate"
    )
    if row["stage"] == "raw":
        java_output = f"{run_prefix}/output.bam"
        final_output = java_output
    else:
        final_output = f"{run_prefix}/output.coordinate.bam"
        java_output = (
            f"{run_prefix}/intermediate.raw.private.bam"
            if raw_sort_order == "unsorted"
            else final_output
        )
    separator = str(workload["umi_separator"])
    upstream_separator = "\\Q" + separator.replace(
        "\\E", "\\E\\\\E\\Q"
    ) + "\\E"
    classpath = ":".join(
        (
            f"<EVIDENCE_DIR>/classes/{source_key}",
            "<EVIDENCE_DIR>/dependencies/htsjdk-3.0.5.jar",
            (
                "<EVIDENCE_DIR>/dependencies/"
                "snappy-java-1.1.10.8.jar"
            ),
        )
    )
    expected_java = [
        "<JAVA>",
        *workload["_validated_jvm_options"],
        f"-Djava.io.tmpdir={run_prefix}/java-tmp",
        "-cp",
        classpath,
        "umicollapse.main.Main",
        "bam",
        "-i",
        (
            f"<EVIDENCE_DIR>/private-inputs/{row['scale']}/"
            "input.private.bam"
        ),
        "-o",
        java_output,
        "-u",
        str(workload["umi_length"]),
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
        separator if source_key == "dumi" else upstream_separator,
    ]
    if workload["paired"] is True:
        expected_java.append("--paired")
    if source_key == "dumi":
        expected_java.extend(["--streaming-mode", row["mode"]])
    expected_java_line = runner_sanitized_shlex_join(expected_java)
    if row["stage"] == "raw":
        expected_command_line = expected_java_line
    else:
        readiness_segments = []
        if raw_sort_order == "unsorted":
            readiness_segments.append(
                runner_sanitized_shlex_join(
                    [
                        "<SAMTOOLS>",
                        "sort",
                        "-o",
                        final_output,
                        java_output,
                    ]
                )
            )
        readiness_segments.append(
            runner_sanitized_shlex_join(
                ["<SAMTOOLS>", "index", final_output]
            )
        )
        nested_command = " && ".join(
            [expected_java_line, *readiness_segments]
        )
        expected_command_line = shlex.join(["bash", "-c", nested_command])
    if command_line != expected_command_line:
        raise ExportError(
            "restricted per-run command differs from the runner contract"
        )


def validate_timed_inspection(
    path: Path,
    *,
    row: Mapping[str, str],
    workload: Mapping[str, object],
    run_root: Path,
) -> None:
    inspection = require_object(
        strict_json(path, "restricted per-run inspection"),
        "restricted per-run inspection",
    )
    require_exact_fields(
        inspection, TIMED_INSPECTION_FIELDS, "restricted per-run inspection"
    )
    expected_output = (
        run_root / "output.bam"
        if row["stage"] == "raw"
        else run_root / "output.coordinate.bam"
    )
    expected_output_file = recorded_path(
        expected_output, run_root.parents[2], "restricted measured output"
    )
    if (
        inspection["quickcheck"] is not True
        or inspection["quickcheck_status"] != "pass"
        or inspection["output_file"] != expected_output_file
        or inspection["actual_route"] != row["actual_route"]
        or inspection["output_records"]
        != nonnegative_integer(row["output_records"], "output record count")
        or inspection["semantic_sha256"] != row["semantic_sha256"]
        or inspection["sort_order"] != row["sort_order"]
        or inspection["output_bytes"]
        != nonnegative_integer(row["output_bytes"], "output byte count")
        or inspection["output_sha256"] != row["output_sha256"]
        or inspection["reference_sequences"]
        != positive_integer(row["reference_sequences"], "reference sequence count")
        or inspection["reference_dictionary_sha256"]
        != row["reference_dictionary_sha256"]
        or inspection["expected_reference_sequences"]
        != positive_integer(
            row["expected_reference_sequences"],
            "expected reference sequence count",
        )
        or inspection["expected_reference_dictionary_sha256"]
        != row["expected_reference_dictionary_sha256"]
        or inspection["exact_oracle_match"]
        is not strict_boolean(row["exact_oracle_match"], "exact oracle match")
    ):
        raise ExportError(
            "restricted per-run inspection does not match its measurement"
        )
    for field in (
        "semantic_sha256",
        "output_sha256",
        "reference_dictionary_sha256",
        "read_group_dictionary_sha256",
        "expected_reference_dictionary_sha256",
        "expected_read_group_dictionary_sha256",
        "alignment_group_output_count_sha256",
        "reference_alignment_group_output_count_sha256",
        "reference_file_sha256",
        "reference_canonical_sha256",
        "reference_cache_receipt_sha256",
    ):
        if not isinstance(inspection[field], str) or not SHA256.fullmatch(
            inspection[field]
        ):
            raise ExportError("restricted per-run inspection has an invalid hash")
    output_records = nonnegative_integer(
        inspection["output_records"], "inspection output records"
    )
    alignment_records = nonnegative_integer(
        inspection["alignment_group_output_records"],
        "inspection alignment-group records",
    )
    excluded_second = nonnegative_integer(
        inspection["alignment_group_records_excluded_second_of_pair"],
        "inspection excluded second-of-pair records",
    )
    excluded_unmapped = nonnegative_integer(
        inspection["alignment_group_records_excluded_unmapped"],
        "inspection excluded unmapped records",
    )
    read_groups = nonnegative_integer(
        inspection["read_groups"], "inspection read groups"
    )
    expected_read_groups = nonnegative_integer(
        inspection["expected_read_groups"], "inspection expected read groups"
    )
    if (
        inspection["alignment_group_fingerprint_version"]
        != ALIGNMENT_GROUP_FINGERPRINT_VERSION
        or inspection["alignment_group_mode"]
        != ("paired" if workload["paired"] else "single-end")
        or alignment_records + excluded_second + excluded_unmapped
        != output_records
        or read_groups != expected_read_groups
        or inspection["read_group_dictionary_sha256"]
        != inspection["expected_read_group_dictionary_sha256"]
        or any(
            inspection[field] is not True
            for field in (
                "alignment_group_output_count_equivalent",
                "alignment_group_output_count_reused_from_exact_reference",
                "record_equivalent",
                "reference_dictionary_equivalent",
                "read_group_dictionary_equivalent",
                "reference_canonical_sha256_verified",
                "reference_cache_receipt_verified",
            )
        )
        or inspection["reference_alignment_group_output_records"]
        != alignment_records
        or inspection["reference_alignment_group_records_excluded_second_of_pair"]
        != excluded_second
        or inspection["reference_alignment_group_records_excluded_unmapped"]
        != excluded_unmapped
        or inspection["reference_alignment_group_output_count_sha256"]
        != inspection["alignment_group_output_count_sha256"]
    ):
        raise ExportError("restricted per-run inspection correctness gate failed")
    reference_prefix = (
        f"<EVIDENCE_DIR>/oracles/external/{row['scale']}/"
        f"{'canonical-upstream' if row['oracle_implementation'] == 'canonical-upstream' else 'dumi-off'}/"
        "output.private.bam"
    )
    rich_oracle = workload["_validated_rich_oracles"][
        row["oracle_implementation"]
    ]
    if (
        inspection["reference_file"] != reference_prefix
        or inspection["reference_file_sha256"]
        != rich_oracle["output_sha256"]
        or inspection["reference_canonical_sha256"]
        != row["expected_semantic_sha256"]
        or inspection["expected_read_groups"]
        != rich_oracle["read_groups"]
        or inspection["expected_read_group_dictionary_sha256"]
        != rich_oracle["read_group_dictionary_sha256"]
        or inspection["alignment_group_output_records"]
        != rich_oracle["alignment_group_output_records"]
        or inspection["alignment_group_records_excluded_second_of_pair"]
        != rich_oracle[
            "alignment_group_records_excluded_second_of_pair"
        ]
        or inspection["alignment_group_records_excluded_unmapped"]
        != rich_oracle["alignment_group_records_excluded_unmapped"]
        or inspection["alignment_group_output_count_sha256"]
        != rich_oracle["alignment_group_output_count_sha256"]
    ):
        raise ExportError("restricted per-run inspection names the wrong oracle")


def validate_per_run_receipts(
    *,
    bundle: Path,
    row: Mapping[str, str],
    workload: Mapping[str, object],
    redacted_paths: set[str],
) -> None:
    run_root, _ = expected_private_run_root(bundle, row)
    expected_paths = {
        "command_file": run_root / "command.txt",
        "stdout_file": run_root / "stdout.txt",
        "stderr_file": run_root / "stderr.txt",
    }
    for field, path in expected_paths.items():
        if row[field] != recorded_path(path, bundle, f"restricted {field}"):
            raise ExportError(f"restricted measurement {field} is not deterministic")
    if row["output_file"]:
        raise ExportError("restricted external measurements retain an output path")
    validate_command_receipt(
        expected_paths["command_file"],
        row=row,
        workload=workload,
        run_root=run_root,
    )
    for field in ("stdout_file", "stderr_file"):
        path = expected_paths[field]
        if read_small_text(path, f"restricted {field}") != REDACTED_LOG_CONTENT:
            raise ExportError("restricted per-run log was not redacted")
        if recorded_path(path, bundle, f"restricted {field}") not in redacted_paths:
            raise ExportError("restricted per-run log is absent from redaction receipt")
    metrics_text = read_small_text(
        run_root / "time.tsv", "restricted GNU-time receipt", maximum=4096
    )
    metric_lines = [line for line in metrics_text.splitlines() if line]
    if len(metric_lines) != 1:
        raise ExportError("restricted GNU-time receipt has an invalid row count")
    fields = metric_lines[0].split("\t")
    if len(fields) != 6:
        raise ExportError("restricted GNU-time receipt has an invalid schema")
    finite_decimal(fields[0], "GNU elapsed time")
    if (
        finite_decimal(fields[1], "GNU user time")
        != finite_decimal(row["user_s"], "measurement user time")
        or finite_decimal(fields[2], "GNU system time")
        != finite_decimal(row["system_s"], "measurement system time")
        or finite_decimal(fields[3], "GNU CPU percentage", percent=True)
        != finite_decimal(row["cpu_pct"], "measurement CPU percentage", percent=True)
        or nonnegative_integer(fields[4], "GNU maximum RSS")
        != nonnegative_integer(row["max_rss_kib"], "measurement maximum RSS")
        or nonnegative_integer(fields[5], "GNU exit code")
        != nonnegative_integer(row["exit_code"], "measurement exit code")
    ):
        raise ExportError("restricted GNU-time receipt does not match measurement")
    monotonic_text = read_small_text(
        run_root / "monotonic-wall-seconds.txt",
        "restricted monotonic-wall receipt",
        maximum=256,
    )
    if (
        not monotonic_text.endswith("\n")
        or monotonic_text.count("\n") != 1
        or finite_decimal(monotonic_text.strip(), "monotonic wall time")
        != finite_decimal(row["elapsed_s"], "measurement elapsed time")
    ):
        raise ExportError(
            "restricted monotonic-wall receipt does not match measurement"
        )
    validate_timed_inspection(
        run_root / "inspection.json",
        row=row,
        workload=workload,
        run_root=run_root,
    )


def sha256_tree(root: Path) -> str:
    if not root.is_dir() or root.is_symlink():
        raise ExportError("restricted build tree is missing")
    digest = hashlib.sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if not files:
        raise ExportError("restricted build tree is empty")
    for path in files:
        if path.is_symlink():
            raise ExportError("restricted build tree contains a symbolic link")
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


@lru_cache(maxsize=16)
def sha256_git_tree(repository: Path, commit: str, prefix: str) -> str:
    """Hash a committed tree using the same path/content contract as sha256_tree."""
    if not GIT_SHA.fullmatch(commit):
        raise ExportError("committed source-tree identity is invalid")
    try:
        listing = subprocess.run(
            [
                "git",
                "-C",
                os.fspath(repository),
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
    except (OSError, subprocess.CalledProcessError) as error:
        raise ExportError("could not inspect the committed source tree") from error

    records = [record for record in listing.split(b"\0") if record]
    if not records:
        raise ExportError("committed source tree is empty")
    expected_prefix = prefix.rstrip("/") + "/"
    entries: list[tuple[str, str]] = []
    for record in records:
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, kind, object_id = metadata.decode("ascii").split()
            path = raw_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as error:
            raise ExportError("committed source-tree inventory is malformed") from error
        if (
            kind != "blob"
            or mode not in {"100644", "100755"}
            or not (
                GIT_SHA.fullmatch(object_id) or SHA256.fullmatch(object_id)
            )
            or not path.startswith(expected_prefix)
        ):
            raise ExportError("committed source-tree inventory is invalid")
        relative = path[len(expected_prefix) :]
        if (
            not relative
            or relative.startswith("/")
            or any(part in {"", ".", ".."} for part in relative.split("/"))
        ):
            raise ExportError("committed source-tree path is invalid")
        entries.append((relative, object_id))

    digest = hashlib.sha256()
    for relative, object_id in sorted(entries):
        try:
            payload = subprocess.run(
                [
                    "git",
                    "-C",
                    os.fspath(repository),
                    "cat-file",
                    "blob",
                    object_id,
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).stdout
        except (OSError, subprocess.CalledProcessError) as error:
            raise ExportError("could not read committed source content") from error
        relative_bytes = relative.encode("utf-8")
        digest.update(len(relative_bytes).to_bytes(8, "big"))
        digest.update(relative_bytes)
        digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()


def expected_build_command(
    label: str,
    sources: Sequence[Path],
    source_root: Path,
) -> str:
    classpath = ":".join(
        f"<EVIDENCE_DIR>/dependencies/{filename}"
        for filename, _digest, _url in LOCKED_DEPENDENCIES
    )
    arguments = [
        "<JAVAC>",
        "--release",
        "11",
        "-cp",
        classpath,
        "-d",
        f"<EVIDENCE_DIR>/classes/{label}",
        *(
            f"<EVIDENCE_DIR>/sources/{label}/src/umicollapse/"
            + source.relative_to(source_root).as_posix()
            for source in sources
        ),
    ]
    return shlex.join(arguments) + "\n"


def validate_method_identity(
    manifest: Mapping[str, object],
    bundle: Path,
    environment: Mapping[str, object],
    dependencies: list[dict[str, object]],
) -> set[str]:
    public_hashes: set[str] = set()
    repository = Path(__file__).resolve().parents[2]
    harness = manifest["harness_files"]
    if not isinstance(harness, list) or len(harness) != len(HARNESS_PATHS):
        raise ExportError("restricted harness inventory is invalid")
    for item, expected_path in zip(harness, HARNESS_PATHS):
        receipt = require_object(item, "harness-file receipt")
        require_exact_fields(
            receipt, HARNESS_FILE_FIELDS, "harness-file receipt"
        )
        digest = receipt["sha256"]
        path = receipt["path"]
        actual = bundle.joinpath(*PurePosixPath(expected_path).parts)
        if (
            path != expected_path
            or not isinstance(digest, str)
            or not SHA256.fullmatch(digest)
            or not actual.is_file()
            or actual.is_symlink()
            or sha256_file(actual) != digest
        ):
            raise ExportError("restricted harness inventory is invalid")
        public_hashes.add(digest)

    binding = require_object(
        manifest["harness_commit_binding"],
        "restricted harness commit binding",
    )
    require_exact_fields(
        binding,
        HARNESS_COMMIT_BINDING_FIELDS,
        "restricted harness commit binding",
    )
    dumi = require_object(manifest["dumi"], "restricted dUMI identity")
    binding_files = binding["files"]
    if (
        binding["status"] != "verified"
        or binding["repository_url"] != DUMI_PUBLIC_URL
        or binding["commit_sha"] != dumi["sha"]
        or not isinstance(binding_files, list)
        or len(binding_files) != len(HARNESS_PATHS)
    ):
        raise ExportError("restricted harness commit binding is invalid")
    for binding_item, harness_item, repository_path, snapshot_path in zip(
        binding_files,
        harness,
        HARNESS_REPOSITORY_PATHS,
        HARNESS_PATHS,
    ):
        receipt = require_object(
            binding_item, "harness commit-binding file"
        )
        require_exact_fields(
            receipt,
            HARNESS_COMMIT_FILE_FIELDS,
            "harness commit-binding file",
        )
        harness_receipt = require_object(
            harness_item, "harness-file receipt"
        )
        archived = bundle.joinpath(
            "sources", "dumi", *PurePosixPath(repository_path).parts
        )
        if (
            receipt["repository_path"] != repository_path
            or receipt["snapshot_path"] != snapshot_path
            or receipt["sha256"] != harness_receipt["sha256"]
            or not archived.is_file()
            or archived.is_symlink()
            or sha256_file(archived) != receipt["sha256"]
        ):
            raise ExportError("restricted harness commit binding is invalid")

    builds = require_object(manifest["builds"], "restricted builds")
    require_exact_fields(builds, {"upstream", "dumi"}, "restricted builds")
    dumi_source_tree_sha256 = sha256_git_tree(
        repository,
        str(dumi["sha"]),
        "src/umicollapse",
    )
    expected_source_tree_sha256 = {
        "upstream": CANONICAL_SOURCE_TREE_SHA256,
        "dumi": dumi_source_tree_sha256,
    }
    for label in ("upstream", "dumi"):
        build = require_object(builds[label], f"{label} build")
        require_exact_fields(build, BUILD_FIELDS, f"{label} build")
        source_tree_digest = build["source_tree_sha256"]
        classes_tree_digest = build["classes_tree_sha256"]
        source_root = bundle / "sources" / label / "src" / "umicollapse"
        classes_root = bundle / "classes" / label
        command_file = bundle / "build-commands" / label / "command.txt"
        sources = sorted(source_root.rglob("*.java"))
        if (
            build["label"] != label
            or build["command_file"]
            != f"build-commands/{label}/command.txt"
            or not command_file.is_file()
            or command_file.is_symlink()
            or not isinstance(source_tree_digest, str)
            or not SHA256.fullmatch(source_tree_digest)
            or source_tree_digest != sha256_tree(source_root)
            or source_tree_digest != expected_source_tree_sha256[label]
            or not isinstance(classes_tree_digest, str)
            or not SHA256.fullmatch(classes_tree_digest)
            or classes_tree_digest != sha256_tree(classes_root)
            or positive_integer(
                build["source_count"], f"{label} source count"
            )
            != len(sources)
        ):
            raise ExportError("restricted build identity is invalid")
        try:
            command_text = command_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise ExportError("restricted build command is unreadable") from error
        if (
            len(command_text.encode("utf-8")) > 1024 * 1024
            or command_text
            != expected_build_command(label, sources, source_root)
        ):
            raise ExportError("restricted build command is invalid")
        public_hashes.update(
            {source_tree_digest, classes_tree_digest}
        )

    archived_lock = bundle / "sources" / "dumi" / "dependencies.lock"
    if (
        not archived_lock.is_file()
        or archived_lock.is_symlink()
        or sha256_file(archived_lock) != DEPENDENCIES_LOCK_SHA256
    ):
        raise ExportError("restricted dependency lock provenance is invalid")

    try:
        runtime_identity = {
            "java": environment["java"],
            "javac": environment["javac"],
            "dependencies": [
                {
                    "filename": dependency["filename"],
                    "sha256": dependency["sha256"],
                }
                for dependency in dependencies
            ],
            "jvm_options": manifest["jvm_options"],
            "cluster_tag_jvm_options": manifest[
                "cluster_tag_jvm_options"
            ],
        }
    except KeyError as error:
        raise ExportError("restricted runtime identity is incomplete") from error
    runtime_id = manifest["runtime_id"]
    expected_runtime_id = hashlib.sha256(
        json.dumps(
            runtime_identity, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    if (
        not isinstance(runtime_id, str)
        or not SHA256.fullmatch(runtime_id)
        or runtime_id != expected_runtime_id
    ):
        raise ExportError("restricted runtime identity is invalid")
    public_hashes.add(runtime_id)
    return public_hashes


def validate_capacity_receipt(
    *,
    bundle: Path,
    source_id: str,
    workload: Mapping[str, object],
    schedule: Mapping[str, object],
    treatment_count: int,
    repetitions: int,
    workload_index: int,
) -> int:
    require_exact_fields(
        schedule,
        TIMING_STAGE_SCHEDULE_FIELDS,
        "timing-stage schedule",
    )
    cells = treatment_count * repetitions
    expected_path = (
        f"inputs/external/{source_id}/stage-scratch-capacity.json"
    )
    if (
        schedule["timing_design_version"] != 2
        or schedule["scope"] != "per-workload"
        or schedule["execution_order"]
        != ["raw", "end_to_end_ready"]
        or schedule["treatments"] != treatment_count
        or schedule["repetitions"] != repetitions
        or schedule["order_family"]
        != (
            "williams-first-order-balanced"
            if treatment_count % 2 == 0
            else "cyclic-latin-fallback-nonreportable"
        )
        or schedule["complete_order_cycles"]
        is not (repetitions % treatment_count == 0)
        or schedule["publication_grade_external_schedule"]
        is not (treatment_count == 4 and repetitions == 8)
        or schedule["raw_cells"] != cells
        or schedule["end_to_end_ready_cells"] != cells
        or schedule["raw_order_offset"] != workload_index
        or schedule["end_to_end_ready_order"]
        != "independent-stage-offset"
        or schedule["end_to_end_ready_order_offset"]
        != workload_index + 1
        or schedule["cross_stage_order_matching_required"] is not False
        or schedule["fresh_deduplication_per_stage_cell"] is not True
        or schedule["validation_and_deletion"]
        != "after-complete-repetition-block"
        or schedule["capacity_receipt"] != expected_path
    ):
        raise ExportError("restricted timing-stage schedule is invalid")
    receipt_path = bundle.joinpath(*PurePosixPath(expected_path).parts)
    if not receipt_path.is_file() or receipt_path.is_symlink():
        raise ExportError("restricted capacity receipt is missing")
    receipt = require_object(
        strict_json(receipt_path, "capacity receipt"),
        "capacity receipt",
    )
    require_exact_fields(
        receipt, CAPACITY_RECEIPT_FIELDS, "capacity receipt"
    )
    integer_fields = CAPACITY_RECEIPT_FIELDS - {
        "directional_oracle_applicable",
        "scope",
        "status",
    }
    numbers = {
        field: nonnegative_integer(receipt[field], f"capacity {field}")
        for field in integer_fields
    }
    input_bytes = numbers["input_bam_bytes"]
    estimated = (input_bytes * 5 + 3) // 4
    retained = treatment_count * 2
    sort_scratch = 1
    timing_peak = estimated * (retained + sort_scratch)
    record_count = numbers["directional_oracle_record_count_upper_bound"]
    umi_length = positive_integer(workload["umi_length"], "UMI length")
    alignment_key = 25 if workload["paired"] is True else 17
    source_keys = record_count * (alignment_key + umi_length + 2)
    tagged_keys = record_count * (alignment_key + 2 * umi_length + 12)
    membership = record_count * (alignment_key + umi_length + 13)
    rooted = record_count * (alignment_key + 2 * umi_length + 14)
    alignment_umi_aggregate = membership
    retained_canonical = 3 * (membership + rooted)
    active_persistent = (
        tagged_keys + retained_canonical + alignment_umi_aggregate
    )
    concurrent_destination_merge = 2 * (
        membership + rooted + alignment_umi_aggregate
    )
    destination_merge = membership + rooted + alignment_umi_aggregate
    sort_buffer_memory = 3 * 256 * 1024 * 1024
    tagged_bam_each = max(
        estimated + record_count * (umi_length + 32),
        input_bytes + record_count * (2 * umi_length + 64),
    )
    tagged_bam_allowance = 2 * tagged_bam_each
    directional_peak = (
        tagged_bam_allowance + active_persistent + destination_merge
    )
    peak = max(timing_peak, directional_peak)
    headroom = max(256 * 1024 * 1024, (peak + 9) // 10)
    required = peak + headroom
    if (
        receipt["status"] != "pass"
        or receipt["scope"]
        != "complete-timing-block-and-deferred-directional-oracle"
        or receipt["directional_oracle_applicable"] is not True
        or input_bytes <= 0
        or record_count <= 0
        or numbers["treatments_per_repetition_block"]
        != treatment_count
        or numbers["retained_block_output_allowances"] != retained
        or numbers["samtools_sort_scratch_allowances"] != sort_scratch
        or numbers["estimated_output_bytes_per_cell"] != estimated
        or numbers["timing_peak_stage_bytes"] != timing_peak
        or numbers["directional_oracle_alignment_key_bytes_per_record"]
        != alignment_key
        or numbers["directional_oracle_source_record_key_bytes"]
        != source_keys
        or numbers["directional_oracle_tagged_record_key_bytes_each"]
        != tagged_keys
        or numbers["directional_oracle_membership_canonical_bytes_each"]
        != membership
        or numbers["directional_oracle_rooted_canonical_bytes_each"]
        != rooted
        or numbers[
            "directional_oracle_alignment_umi_aggregate_bytes_each"
        ]
        != alignment_umi_aggregate
        or numbers["directional_oracle_retained_canonical_bytes"]
        != retained_canonical
        or numbers["directional_oracle_active_persistent_bytes"]
        != active_persistent
        or numbers[
            "directional_oracle_concurrent_sort_destination_merge_bytes"
        ]
        != concurrent_destination_merge
        or numbers["directional_oracle_sort_destination_merge_bytes"]
        != destination_merge
        or numbers[
            "directional_oracle_concurrent_sort_buffer_memory_bytes"
        ]
        != sort_buffer_memory
        or numbers["directional_oracle_tagged_bam_bytes_each"]
        != tagged_bam_each
        or numbers["directional_oracle_tagged_bam_allowance_bytes"]
        != tagged_bam_allowance
        or numbers["directional_oracle_peak_stage_bytes"]
        != directional_peak
        or numbers["peak_stage_output_bytes"] != peak
        or numbers["headroom_bytes"] != headroom
        or numbers["required_available_bytes"] != required
        or numbers["available_bytes"] < required
        or schedule["capacity_status"] != receipt["status"]
        or schedule["capacity_required_available_bytes"] != required
        or schedule["capacity_available_bytes"] != numbers["available_bytes"]
        or schedule["capacity_timing_peak_stage_bytes"] != timing_peak
        or schedule["capacity_directional_oracle_peak_stage_bytes"]
        != directional_peak
    ):
        raise ExportError("restricted capacity receipt is invalid")
    workload["_validated_capacity_record_count"] = record_count
    workload["_validated_capacity_receipt"] = dict(receipt)
    return input_bytes


def validate_directional_oracle_evidence(
    *,
    bundle: Path,
    source_id: str,
    workload: dict[str, object],
    directional_manifest: Mapping[str, object],
    pairwise_manifest: Mapping[str, object],
    performance_manifest: Mapping[str, object],
) -> None:
    require_exact_fields(
        directional_manifest,
        DIRECTIONAL_MANIFEST_GATE_FIELDS,
        "directional-oracle manifest gate",
    )
    require_exact_fields(
        pairwise_manifest,
        PAIRWISE_MANIFEST_DIAGNOSTIC_FIELDS,
        "pairwise cluster diagnostic",
    )
    require_exact_fields(
        performance_manifest,
        PERFORMANCE_COMPARABILITY_FIELDS,
        "performance comparability",
    )
    directional_relative = (
        f"oracles/external/{source_id}/directional-oracle-receipt.json"
    )
    pairwise_relative = (
        f"oracles/external/{source_id}/"
        "pairwise-cluster-diagnostic-receipt.json"
    )
    post_capacity_relative = (
        f"inputs/external/{source_id}/"
        "post-timing-oracle-scratch-capacity.json"
    )
    directional_path = bundle.joinpath(
        *PurePosixPath(directional_relative).parts
    )
    pairwise_path = bundle.joinpath(*PurePosixPath(pairwise_relative).parts)
    post_capacity_path = bundle.joinpath(
        *PurePosixPath(post_capacity_relative).parts
    )
    for path, context in (
        (directional_path, "directional-oracle receipt"),
        (pairwise_path, "pairwise cluster receipt"),
        (post_capacity_path, "post-timing capacity receipt"),
    ):
        if not path.is_file() or path.is_symlink():
            raise ExportError(f"restricted {context} is missing")

    directional = require_object(
        strict_json(directional_path, "directional-oracle receipt"),
        "directional-oracle receipt",
    )
    require_exact_fields(
        directional,
        DIRECTIONAL_RECEIPT_FIELDS,
        "directional-oracle receipt",
    )
    methods = require_object(
        directional["methods"], "directional-oracle methods"
    )
    configuration = require_object(
        directional["configuration"],
        "directional-oracle configuration",
    )
    gate = require_object(
        directional["gate"], "directional-oracle gate"
    )
    diagnostics = require_object(
        directional["diagnostics"],
        "directional-oracle diagnostics",
    )
    provenance = require_object(
        directional["provenance"],
        "directional-oracle provenance",
    )
    temporary_storage = require_object(
        directional["temporary_storage"],
        "directional-oracle temporary storage",
    )
    require_exact_fields(
        configuration,
        DIRECTIONAL_CONFIGURATION_FIELDS,
        "directional-oracle configuration",
    )
    require_exact_fields(gate, DIRECTIONAL_GATE_FIELDS, "directional-oracle gate")
    require_exact_fields(
        diagnostics,
        DIRECTIONAL_DIAGNOSTIC_FIELDS,
        "directional-oracle diagnostics",
    )
    require_exact_fields(
        provenance,
        DIRECTIONAL_PROVENANCE_FIELDS,
        "directional-oracle provenance",
    )
    require_exact_fields(
        temporary_storage,
        DIRECTIONAL_TEMPORARY_STORAGE_FIELDS,
        "directional-oracle temporary storage",
    )
    separator = str(workload["umi_separator"]).encode("ascii")
    expected_configuration = {
        "mode": "paired" if workload["paired"] is True else "single-end",
        "umi_length": workload["umi_length"],
        "umi_separator_bytes": len(separator),
        "umi_separator_sha256": hashlib.sha256(separator).hexdigest(),
        "edit_distance": 1,
        "percentage_decimal": "0.5",
        "percentage_binary32_hex": "3f000000",
        "remove_unpaired": False,
        "remove_chimeric": False,
        "sort_buffer_size": "256M",
    }
    if (
        directional["schema"] != DIRECTIONAL_ORACLE_SCHEMA
        or directional["version"] != DIRECTIONAL_ORACLE_SCHEMA_VERSION
        or isinstance(directional["version"], bool)
        or methods != DIRECTIONAL_ORACLE_METHODS
        or configuration != expected_configuration
        or any(not isinstance(gate[field], bool) for field in gate)
        or any(
            not isinstance(diagnostics[field], bool)
            for field in diagnostics
        )
        or gate["directional_oracle_gate_pass"]
        is not all(
            gate[field]
            for field in DIRECTIONAL_GATE_FIELDS
            if field != "directional_oracle_gate_pass"
        )
        or gate["directional_oracle_gate_pass"] is not True
    ):
        raise ExportError("restricted directional-oracle receipt is invalid")

    metric_fields = (
        DIRECTIONAL_METRIC_COUNT_FIELDS | DIRECTIONAL_METRIC_HASH_FIELDS
    )
    metrics: dict[str, dict[str, object]] = {}
    for label in ("source_oracle", "canonical_upstream", "dumi_off"):
        value = require_object(
            directional[label], f"directional-oracle {label} metrics"
        )
        require_exact_fields(
            value,
            metric_fields,
            f"directional-oracle {label} metrics",
        )
        if (
            any(
                isinstance(value[field], bool)
                or not isinstance(value[field], int)
                or value[field] < 0
                for field in DIRECTIONAL_METRIC_COUNT_FIELDS
            )
            or any(
                not isinstance(value[field], str)
                or not SHA256.fullmatch(value[field])
                for field in DIRECTIONAL_METRIC_HASH_FIELDS
            )
            or value["records"] != value["eligible_records"]
            or value["eligible_records"] <= 0
            or value["input_bytes"] <= 0
            or not (
                1
                <= value["alignment_groups"]
                <= value["clusters"]
                <= value["umi_memberships"]
                <= value["eligible_records"]
            )
            or not (
                1
                <= value["max_umi_memberships_per_cluster"]
                <= value["umi_memberships"]
            )
            or value["membership_partition_bytes"] <= 0
            or value["rooted_partition_bytes"] <= 0
            or value["alignment_umi_frequency_multiset_bytes"] <= 0
            or value["record_key_bytes"] <= 0
            or value["reference_sequences"] <= 0
            or value["input_records"]
            != (
                value["eligible_records"]
                + value["excluded_unmapped"]
                + value["excluded_second_of_pair"]
                + value["excluded_unpaired"]
                + value["excluded_mate_unmapped"]
                + value["excluded_chimeric"]
            )
            or value["excluded_unpaired"] != 0
            or value["excluded_chimeric"] != 0
        ):
            raise ExportError(
                f"restricted directional-oracle {label} metrics are invalid"
            )
        metrics[label] = value

    def same_partition(
        left: Mapping[str, object],
        right: Mapping[str, object],
        bytes_field: str,
        hash_field: str,
    ) -> bool:
        return (
            left[bytes_field] == right[bytes_field]
            and left[hash_field] == right[hash_field]
        )

    source = metrics["source_oracle"]
    upstream = metrics["canonical_upstream"]
    dumi = metrics["dumi_off"]
    expected_gate = {
        "dumi_off_oracle_partition_equivalent": same_partition(
            dumi,
            source,
            "membership_partition_bytes",
            "membership_partition_sha256",
        ),
        "dumi_off_oracle_root_assignment_equivalent": same_partition(
            dumi,
            source,
            "rooted_partition_bytes",
            "rooted_partition_sha256",
        ),
        "dumi_off_source_reference_dictionary_equivalent": same_partition(
            dumi,
            source,
            "reference_sequences",
            "reference_dictionary_sha256",
        ),
        "dumi_off_source_read_group_dictionary_equivalent": same_partition(
            dumi,
            source,
            "read_groups",
            "read_group_dictionary_sha256",
        ),
    }
    expected_diagnostics = {
        "canonical_upstream_oracle_partition_equivalent": same_partition(
            upstream,
            source,
            "membership_partition_bytes",
            "membership_partition_sha256",
        ),
        "canonical_upstream_oracle_root_assignment_equivalent": same_partition(
            upstream,
            source,
            "rooted_partition_bytes",
            "rooted_partition_sha256",
        ),
        "canonical_upstream_dumi_off_partition_equivalent": same_partition(
            upstream,
            dumi,
            "membership_partition_bytes",
            "membership_partition_sha256",
        ),
        "canonical_upstream_dumi_off_root_assignment_equivalent": (
            same_partition(
                upstream,
                dumi,
                "rooted_partition_bytes",
                "rooted_partition_sha256",
            )
        ),
        "canonical_upstream_source_reference_dictionary_equivalent": (
            same_partition(
                upstream,
                source,
                "reference_sequences",
                "reference_dictionary_sha256",
            )
        ),
        "canonical_upstream_source_read_group_dictionary_equivalent": (
            same_partition(
                upstream,
                source,
                "read_groups",
                "read_group_dictionary_sha256",
            )
        ),
    }
    if any(gate[field] is not value for field, value in expected_gate.items()):
        raise ExportError(
            "restricted directional-oracle gate contradicts its evidence"
        )
    if any(
        diagnostics[field] is not value
        for field, value in expected_diagnostics.items()
    ):
        raise ExportError(
            "restricted directional-oracle diagnostics contradict their evidence"
        )
    if any(
        dumi[field] != source[field]
        for field in (
            "records",
            "alignment_groups",
            "clusters",
            "umi_memberships",
        )
    ):
        raise ExportError(
            "restricted passing directional-oracle gate has inconsistent "
            "dUMI/source aggregate metrics"
        )
    alignment_frequency_receipts = {
        (
            metrics[label]["alignment_umi_frequency_multiset_bytes"],
            metrics[label]["alignment_umi_frequency_multiset_sha256"],
        )
        for label in ("source_oracle", "canonical_upstream", "dumi_off")
    }
    if len(alignment_frequency_receipts) != 1:
        raise ExportError(
            "restricted directional-oracle eligible-record evidence differs"
        )
    if source["input_sha256"] != directional_manifest["input_sha256"]:
        raise ExportError(
            "restricted directional-oracle input identity is inconsistent"
        )
    if (
        source["input_bytes"]
        != workload["_validated_capacity_input_bytes"]
        or source["input_records"]
        != workload["_validated_capacity_record_count"]
    ):
        raise ExportError(
            "restricted directional-oracle source size is inconsistent"
        )

    source_partitions = int(source["membership_partition_bytes"]) + int(
        source["rooted_partition_bytes"]
    )
    upstream_partitions = int(
        upstream["membership_partition_bytes"]
    ) + int(upstream["rooted_partition_bytes"])
    dumi_partitions = int(dumi["membership_partition_bytes"]) + int(
        dumi["rooted_partition_bytes"]
    )
    oracle_peak = max(
        int(source["record_key_bytes"])
        + source_partitions
        + int(source["alignment_umi_frequency_multiset_bytes"]),
        source_partitions
        + int(upstream["record_key_bytes"])
        + upstream_partitions
        + int(upstream["alignment_umi_frequency_multiset_bytes"]),
        source_partitions
        + upstream_partitions
        + int(dumi["record_key_bytes"])
        + dumi_partitions
        + int(dumi["alignment_umi_frequency_multiset_bytes"]),
    )
    if (
        temporary_storage["persistent_stage_peak_upper_bound_bytes"]
        != oracle_peak
        or temporary_storage["sort_merge_storage_note"]
        != (
            "bounded external-sort merge files are additional and "
            "scale linearly with the active stream"
        )
    ):
        raise ExportError(
            "restricted directional-oracle storage receipt is invalid"
        )
    expected_helper_hashes = {
        "helper_sha256": sha256_file(
            bundle / "harness" / "directional_oracle_check.py"
        ),
        "partition_checker_sha256": sha256_file(
            bundle / "harness" / "cluster_partition_check.py"
        ),
        "private_streams_retained": False,
    }
    if provenance != expected_helper_hashes:
        raise ExportError(
            "restricted directional-oracle helper provenance is invalid"
        )

    pairwise = require_object(
        strict_json(pairwise_path, "pairwise cluster receipt"),
        "pairwise cluster receipt",
    )
    require_exact_fields(
        pairwise, PAIRWISE_RECEIPT_FIELDS, "pairwise cluster receipt"
    )
    pairwise_configuration = require_object(
        pairwise["configuration"], "pairwise cluster configuration"
    )
    require_exact_fields(
        pairwise_configuration,
        PAIRWISE_CONFIGURATION_FIELDS,
        "pairwise cluster configuration",
    )
    expected_pairwise_configuration = {
        key: expected_configuration[key]
        for key in PAIRWISE_CONFIGURATION_FIELDS
    }
    pairwise_booleans = {
        "equivalent": pairwise["equivalent"],
        "partition_equivalent": pairwise["partition_equivalent"],
        "reference_dictionary_equivalent": pairwise[
            "reference_dictionary_equivalent"
        ],
        "read_group_dictionary_equivalent": pairwise[
            "read_group_dictionary_equivalent"
        ],
    }
    sides: dict[str, dict[str, object]] = {}
    for name in ("left", "right"):
        side = require_object(pairwise[name], f"pairwise cluster {name}")
        require_exact_fields(
            side,
            PAIRWISE_SIDE_COUNT_FIELDS | PAIRWISE_SIDE_HASH_FIELDS,
            f"pairwise cluster {name}",
        )
        if any(
            isinstance(side[field], bool)
            or not isinstance(side[field], int)
            or side[field] < 0
            for field in PAIRWISE_SIDE_COUNT_FIELDS
        ) or any(
            not isinstance(side[field], str)
            or not SHA256.fullmatch(side[field])
            for field in PAIRWISE_SIDE_HASH_FIELDS
        ):
            raise ExportError("restricted pairwise cluster metrics are invalid")
        sides[name] = side
    expected_pairwise_booleans = {
        "partition_equivalent": (
            sides["left"]["canonical_partition_bytes"]
            == sides["right"]["canonical_partition_bytes"]
            and sides["left"]["partition_cluster_multiset_sha256"]
            == sides["right"]["partition_cluster_multiset_sha256"]
        ),
        "reference_dictionary_equivalent": (
            sides["left"]["reference_sequences"]
            == sides["right"]["reference_sequences"]
            and sides["left"]["reference_dictionary_sha256"]
            == sides["right"]["reference_dictionary_sha256"]
        ),
        "read_group_dictionary_equivalent": (
            sides["left"]["read_groups"]
            == sides["right"]["read_groups"]
            and sides["left"]["read_group_dictionary_sha256"]
            == sides["right"]["read_group_dictionary_sha256"]
        ),
    }
    expected_pairwise_booleans["equivalent"] = all(
        expected_pairwise_booleans.values()
    )
    if (
        pairwise["schema"] != "dumi-cluster-partition-check-v1"
        or pairwise["partition_fingerprint_version"]
        != "umicollapse-tag-alignment-cluster-umi-frequency-v1"
        or pairwise_configuration != expected_pairwise_configuration
        or any(not isinstance(value, bool) for value in pairwise_booleans.values())
        or any(
            pairwise_booleans[field] is not value
            for field, value in expected_pairwise_booleans.items()
        )
    ):
        raise ExportError("restricted pairwise cluster receipt is invalid")
    pairwise_directional_mapping = {
        "input_records": "input_records",
        "eligible_records": "eligible_records",
        "excluded_unmapped": "excluded_unmapped",
        "excluded_second_of_pair": "excluded_second_of_pair",
        "excluded_unpaired": "excluded_unpaired",
        "excluded_mate_unmapped": "excluded_mate_unmapped",
        "excluded_chimeric": "excluded_chimeric",
        "alignment_groups": "alignment_groups",
        "clusters": "clusters",
        "umi_memberships": "umi_memberships",
        "max_umi_memberships_per_cluster": (
            "max_umi_memberships_per_cluster"
        ),
        "record_key_bytes": "record_key_bytes",
        "canonical_partition_bytes": "membership_partition_bytes",
        "partition_cluster_multiset_sha256": (
            "membership_partition_sha256"
        ),
        "reference_sequences": "reference_sequences",
        "reference_dictionary_sha256": "reference_dictionary_sha256",
        "read_groups": "read_groups",
        "read_group_dictionary_sha256": "read_group_dictionary_sha256",
    }
    for name, directional_metrics in (
        ("left", upstream),
        ("right", dumi),
    ):
        side = sides[name]
        if any(
            side[pairwise_field] != directional_metrics[directional_field]
            for pairwise_field, directional_field
            in pairwise_directional_mapping.items()
        ):
            raise ExportError(
                "restricted directional and pairwise receipts are inconsistent"
            )
    pairwise_storage = require_object(
        pairwise["temporary_storage"],
        "pairwise temporary storage",
    )
    require_exact_fields(
        pairwise_storage,
        PAIRWISE_TEMPORARY_STORAGE_FIELDS,
        "pairwise temporary storage",
    )
    expected_pairwise_peak = int(sides["left"]["canonical_partition_bytes"]) + max(
        int(sides["right"]["record_key_bytes"])
        + int(sides["right"]["canonical_partition_bytes"]),
        int(sides["left"]["record_key_bytes"])
        + int(sides["left"]["canonical_partition_bytes"]),
    )
    if (
        pairwise_storage["persistent_stage_peak_upper_bound_bytes"]
        != expected_pairwise_peak
        or pairwise_storage["sort_merge_storage_note"]
        != (
            "bounded external-sort merge files are additional and "
            "scale linearly with the active stage"
        )
    ):
        raise ExportError("restricted pairwise storage receipt is invalid")

    post_capacity = require_object(
        strict_json(post_capacity_path, "post-timing capacity receipt"),
        "post-timing capacity receipt",
    )
    require_exact_fields(
        post_capacity,
        CAPACITY_RECEIPT_FIELDS,
        "post-timing capacity receipt",
    )
    initial_capacity = require_object(
        workload["_validated_capacity_receipt"],
        "validated initial capacity receipt",
    )
    shared_capacity_fields = CAPACITY_RECEIPT_FIELDS - {
        "available_bytes",
        "headroom_bytes",
        "peak_stage_output_bytes",
        "required_available_bytes",
        "scope",
        "status",
    }
    post_peak = nonnegative_integer(
        post_capacity["directional_oracle_peak_stage_bytes"],
        "post-timing directional-oracle peak",
    )
    post_headroom = max(256 * 1024 * 1024, (post_peak + 9) // 10)
    post_required = post_peak + post_headroom
    if (
        any(
            post_capacity[field] != initial_capacity[field]
            for field in shared_capacity_fields
        )
        or post_capacity["scope"] != "deferred-directional-oracle-only"
        or post_capacity["peak_stage_output_bytes"] != post_peak
        or post_capacity["headroom_bytes"] != post_headroom
        or post_capacity["required_available_bytes"] != post_required
        or post_capacity["status"] != "pass"
        or nonnegative_integer(
            post_capacity["available_bytes"],
            "post-timing available capacity",
        )
        < nonnegative_integer(
            post_capacity["required_available_bytes"],
            "post-timing required capacity",
        )
    ):
        raise ExportError("restricted post-timing capacity receipt is invalid")

    directional_digest = sha256_file(directional_path)
    pairwise_digest = sha256_file(pairwise_path)
    manifest_gate_values = {
        field: gate[field] for field in DIRECTIONAL_GATE_FIELDS
    }
    if (
        directional_manifest["applicable"] is not True
        or directional_manifest["status"] != "pass"
        or directional_manifest["input"] != "verified-private-timing-snapshot"
        or directional_manifest["receipt"] != directional_relative
        or directional_manifest["receipt_sha256"] != directional_digest
        or directional_manifest["methods"] != methods
        or directional_manifest["diagnostics"] != diagnostics
        or any(
            directional_manifest[field] is not value
            for field, value in manifest_gate_values.items()
        )
        or directional_manifest["untimed"] is not True
        or directional_manifest["tagged_outputs_retained"] is not False
        or directional_manifest["private_oracle_streams_retained"] is not False
        or directional_manifest["post_timing_capacity_receipt"]
        != post_capacity_relative
        or directional_manifest[
            "post_timing_capacity_required_available_bytes"
        ]
        != post_capacity["required_available_bytes"]
        or directional_manifest[
            "post_timing_capacity_available_bytes"
        ]
        != post_capacity["available_bytes"]
        or pairwise_manifest["applicable"] is not True
        or pairwise_manifest["status"]
        != ("match" if pairwise["equivalent"] is True else "difference")
        or pairwise_manifest["scope"] != "diagnostic-only"
        or pairwise_manifest["equivalent"] is not pairwise["equivalent"]
        or pairwise_manifest["partition_equivalent"]
        is not pairwise["partition_equivalent"]
        or pairwise_manifest["reference_dictionary_equivalent"]
        is not pairwise["reference_dictionary_equivalent"]
        or pairwise_manifest["read_group_dictionary_equivalent"]
        is not pairwise["read_group_dictionary_equivalent"]
        or pairwise_manifest["receipt"] != pairwise_relative
        or pairwise_manifest["receipt_sha256"] != pairwise_digest
        or pairwise_manifest["untimed"] is not True
        or pairwise_manifest["tagged_outputs_retained"] is not False
        or pairwise_manifest["private_partition_streams_retained"] is not False
    ):
        raise ExportError(
            "restricted workload oracle manifests are inconsistent"
        )

    comparable = performance_manifest[
        "cross_implementation_output_count_match"
    ]
    if not isinstance(comparable, bool):
        raise ExportError("restricted performance comparability is invalid")
    expected_issues = [] if comparable else [NONCOMPARABLE_OUTPUT_COUNT_ISSUE]
    if (
        performance_manifest["applicable"] is not True
        or performance_manifest["status"]
        != ("comparable" if comparable else "not_comparable")
        or performance_manifest["issues"] != expected_issues
        or not isinstance(
            performance_manifest[
                "cross_implementation_exact_match"
            ],
            bool,
        )
        or not isinstance(
            performance_manifest[
                "cross_implementation_alignment_group_output_count_match"
            ],
            bool,
        )
    ):
        raise ExportError("restricted performance comparability is invalid")

    workload["_validated_directional_receipt"] = directional_relative
    workload["_validated_directional_public_method"] = {
        "schema": directional["schema"],
        "version": directional["version"],
        "methods": dict(methods),
        "independent_components": list(
            DIRECTIONAL_PUBLIC_INDEPENDENT_COMPONENTS
        ),
        "shared_transport_components": list(
            DIRECTIONAL_PUBLIC_SHARED_TRANSPORT_COMPONENTS
        ),
    }
    workload["_validated_directional_gate"] = dict(gate)
    workload["_validated_directional_diagnostics"] = dict(diagnostics)
    workload["_validated_pairwise_diagnostic"] = dict(pairwise_booleans)
    workload["_validated_performance_comparability"] = dict(
        performance_manifest
    )
    workload["_validated_directional_input_sha256"] = source["input_sha256"]
    workload["_validated_directional_input_bytes"] = source["input_bytes"]
    workload["_validated_directional_input_records"] = source[
        "input_records"
    ]
    workload["_validated_directional_reference_sequences"] = source[
        "reference_sequences"
    ]
    workload["_validated_directional_reference_dictionary_sha256"] = source[
        "reference_dictionary_sha256"
    ]
    workload["_validated_directional_source_metrics"] = dict(source)


def validate_forced_on_contract(
    value: object,
    *,
    eligible: bool,
    paired: bool,
) -> None:
    receipt = require_object(value, "forced-on contract")
    require_exact_fields(receipt, FORCED_ON_FIELDS, "forced-on contract")
    common_valid = (
        receipt["status"] == "pass"
        and receipt["eligible"] is eligible
        and receipt["timed_cell_scheduled"] is eligible
        and receipt["logs_suppressed"] is True
        and receipt["fallback_marker_seen"] is False
    )
    if eligible:
        specific_valid = (
            receipt["exit_code"] == 0
            and receipt["output_created"] is True
            and receipt["streaming_marker_seen"] is True
            and receipt["observed_route"] == "streaming"
            and receipt["observed_sort_order"] == "unsorted"
            and receipt["rejection_reason"] is None
        )
    elif paired:
        specific_valid = (
            receipt["exit_code"] == 2
            and receipt["output_created"] is False
            and receipt["streaming_marker_seen"] is False
            and receipt["observed_route"] == "rejected-ineligible"
            and receipt["observed_sort_order"] is None
            and receipt["rejection_reason"] == "paired-mode-incompatible"
        )
    else:
        reason = receipt["rejection_reason"]
        specific_valid = (
            receipt["exit_code"] == 1
            and receipt["output_created"] is False
            and receipt["streaming_marker_seen"] is True
            and receipt["observed_route"] == "rejected-ineligible"
            and receipt["observed_sort_order"] is None
            and reason in SINGLE_END_FORCED_ON_REJECTION_REASONS
        )
    if not common_valid or not specific_valid:
        raise ExportError("restricted forced-on contract is invalid")


def validate_restricted_environment(
    environment: Mapping[str, object],
    manifest_environment: object,
) -> None:
    require_allowed_fields(
        environment,
        ENVIRONMENT_ALLOWED_FIELDS,
        ENVIRONMENT_REQUIRED_FIELDS,
        "restricted environment",
    )
    if environment["environment_policy"] != "allowlist":
        raise ExportError("restricted environment policy is invalid")
    subprocess_environment = require_object(
        environment["subprocess_environment"],
        "restricted subprocess environment",
    )
    require_exact_fields(
        subprocess_environment,
        SUBPROCESS_ENVIRONMENT_FIELDS,
        "restricted subprocess environment",
    )
    if manifest_environment != subprocess_environment:
        raise ExportError(
            "restricted manifest and environment policies are inconsistent"
        )
    if (
        subprocess_environment["LANG"] != "C"
        or subprocess_environment["LC_ALL"] != "C"
        or subprocess_environment["TZ"] != "UTC"
        or subprocess_environment["HOME"]
        != "<EVIDENCE_DIR>/process-home"
        or subprocess_environment["TMPDIR"]
        != "<EVIDENCE_DIR>/process-tmp"
    ):
        raise ExportError("restricted subprocess environment is invalid")
    path = safe_one_line(
        subprocess_environment["PATH"],
        "restricted subprocess PATH",
        maximum=4096,
    )
    if any(character.isspace() for character in path):
        raise ExportError("restricted subprocess PATH is invalid")
    removed = environment.get("removed_injection_environment_variables")
    network = environment.get("network_environment_variable_names")
    if (
        not isinstance(removed, list)
        or removed != sorted(set(removed))
        or not all(
            isinstance(name, str)
            and name in INJECTION_ENVIRONMENT_VARIABLES
            for name in removed
        )
        or not isinstance(network, list)
        or network != sorted(set(network))
        or not all(
            isinstance(name, str)
            and name in NETWORK_ENVIRONMENT_VARIABLES
            for name in network
        )
    ):
        raise ExportError("restricted environment variable policy is invalid")


def validate_oracle_receipts(
    *,
    bundle: Path,
    source_id: str,
    workload: Mapping[str, object],
    canonical_sha: str,
    dumi_sha: str,
) -> tuple[dict[str, dict[str, str]], bool]:
    path = (
        bundle / "inputs" / "external" / source_id / "hashes.json"
    )
    if not path.is_file() or path.is_symlink():
        raise ExportError("restricted input-hash receipt is missing")
    receipt = require_object(
        strict_json(path, "input-hash receipt"), "input-hash receipt"
    )
    require_exact_fields(
        receipt, INPUT_HASH_RECEIPT_FIELDS, "input-hash receipt"
    )
    bam = require_object(receipt["bam"], "input-hash BAM receipt")
    validation = require_object(
        receipt["validation"], "input-hash validation receipt"
    )
    oracles = require_object(receipt["oracles"], "input-hash oracles")
    cross = require_object(
        receipt["cross_implementation_diagnostic"],
        "cross-implementation receipt",
    )
    require_exact_fields(bam, INPUT_HASH_BAM_FIELDS, "input-hash BAM receipt")
    require_exact_fields(
        validation,
        INPUT_HASH_VALIDATION_FIELDS,
        "input-hash validation receipt",
    )
    require_exact_fields(
        oracles, {"canonical_upstream", "dumi"}, "input-hash oracles"
    )
    require_exact_fields(
        cross, CROSS_IMPLEMENTATION_RECEIPT_FIELDS, "cross-implementation receipt"
    )
    if (
        receipt["input_mode"] != "external_bam"
        or receipt["workload_id"] != source_id
        or receipt["paired"] is not workload["paired"]
        or receipt["umi_length"] != workload["umi_length"]
        or receipt["umi_separator"] != workload["umi_separator"]
        or receipt["rationale_provided"]
        is not workload["rationale_provided"]
        or bam["bytes"] != workload["_validated_capacity_input_bytes"]
        or bam["sha256"] != workload["_validated_input_sha256"]
        or bam["path_recorded"] is not False
        or validation["quickcheck_status"] != "pass"
        or validation["declared_sort_order"] != "coordinate"
        or validation["temporary_index_validation"] != "pass"
    ):
        raise ExportError("restricted input-hash receipt is inconsistent")

    normalized: dict[str, dict[str, str]] = {}
    for name, expected_implementation, expected_source_sha in (
        ("canonical_upstream", "canonical-upstream", canonical_sha),
        ("dumi", "dumi", dumi_sha),
    ):
        oracle = require_object(oracles[name], f"{name} oracle identity")
        expected_fields = (
            ORACLE_IDENTITY_FIELDS | {"mode"}
            if name == "dumi"
            else ORACLE_IDENTITY_FIELDS
        )
        require_exact_fields(
            oracle, expected_fields, f"{name} oracle identity"
        )
        if (
            oracle["implementation"] != expected_implementation
            or oracle["source_sha"] != expected_source_sha
            or oracle["kind"] != "untimed_exact_implementation_oracle"
            or oracle["timed"] is not False
            or oracle["output_retained"] is not False
            or (name == "dumi" and oracle["mode"] != "off")
            or not isinstance(oracle["semantic_sha256"], str)
            or not SHA256.fullmatch(oracle["semantic_sha256"])
            or not isinstance(oracle["reference_dictionary_sha256"], str)
            or not SHA256.fullmatch(
                oracle["reference_dictionary_sha256"]
            )
        ):
            raise ExportError("restricted oracle identity is invalid")
        output_records = positive_integer(
            oracle["output_records"], f"{name} oracle output records"
        )
        reference_sequences = positive_integer(
            oracle["reference_sequences"],
            f"{name} oracle reference sequences",
        )
        if (
            reference_sequences
            != workload["_validated_reference_sequences"]
            or oracle["reference_dictionary_sha256"]
            != workload["_validated_reference_dictionary_sha256"]
        ):
            raise ExportError("restricted oracle header is inconsistent")
        normalized[
            "canonical-upstream" if name == "canonical_upstream" else "dumi-off"
        ] = {
            "expected_output_records": str(output_records),
            "expected_semantic_sha256": oracle["semantic_sha256"],
            "expected_reference_sequences": str(reference_sequences),
            "expected_reference_dictionary_sha256": oracle[
                "reference_dictionary_sha256"
            ],
        }
    exact_match = (
        normalized["canonical-upstream"]["expected_output_records"]
        == normalized["dumi-off"]["expected_output_records"]
        and normalized["canonical-upstream"]["expected_semantic_sha256"]
        == normalized["dumi-off"]["expected_semantic_sha256"]
    )
    output_count_match = (
        normalized["canonical-upstream"]["expected_output_records"]
        == normalized["dumi-off"]["expected_output_records"]
    )
    boolean_cross_fields = CROSS_IMPLEMENTATION_RECEIPT_FIELDS - {
        "scope",
        "status",
    }
    if any(not isinstance(cross[field], bool) for field in boolean_cross_fields):
        raise ExportError("restricted cross-implementation receipt is invalid")
    diagnostic_match = all(
        cross[field]
        for field in (
            "record_counts_equal",
            "alignment_group_output_record_counts_equal",
            "excluded_unmapped_counts_equal",
            "excluded_second_of_pair_counts_equal",
            "ordered_sq_equal",
            "ordered_rg_equal",
            "alignment_group_output_count_match",
            "alignment_group_output_count_multiset_equal",
        )
    )
    if (
        cross["scope"] != "diagnostic-only"
        or cross["status"] != ("match" if diagnostic_match else "difference")
        or cross["exact_match"] is not exact_match
        or cross["output_count_match"] is not output_count_match
        or cross["record_counts_equal"] is not output_count_match
        or cross["alignment_group_output_count_match"]
        is not cross["alignment_group_output_count_multiset_equal"]
    ):
        raise ExportError("restricted cross-implementation receipt is invalid")
    rich_oracles = validate_rich_oracle_receipts(
        bundle=bundle,
        source_id=source_id,
        workload=workload,
        normalized=normalized,
        cross=cross,
        exact_match=exact_match,
    )
    workload["_validated_rich_oracles"] = rich_oracles
    workload["_validated_cross_receipt"] = dict(cross)
    performance = require_object(
        workload["_validated_performance_comparability"],
        "validated performance comparability",
    )
    if (
        performance["cross_implementation_exact_match"] is not exact_match
        or performance["cross_implementation_output_count_match"]
        is not output_count_match
        or performance[
            "cross_implementation_alignment_group_output_count_match"
        ]
        is not cross["alignment_group_output_count_match"]
    ):
        raise ExportError(
            "restricted performance comparability contradicts oracle evidence"
        )
    workload["_validated_cross_output_count"] = output_count_match
    workload["_validated_cross_alignment_group"] = cross[
        "alignment_group_output_count_match"
    ]
    workload["_validated_cross_bounded_diagnostic"] = (
        diagnostic_match
        and workload["_validated_pairwise_diagnostic"]["equivalent"] is True
    )
    return normalized, exact_match


def validate_rich_oracle_receipts(
    *,
    bundle: Path,
    source_id: str,
    workload: Mapping[str, object],
    normalized: Mapping[str, Mapping[str, str]],
    cross: Mapping[str, object],
    exact_match: bool,
) -> dict[str, dict[str, object]]:
    root = bundle / "oracles" / "external" / source_id
    rich_cross = require_object(
        strict_json(
            root / "cross-implementation-receipt.json",
            "retained cross-implementation receipt",
        ),
        "retained cross-implementation receipt",
    )
    require_exact_fields(
        rich_cross,
        CROSS_IMPLEMENTATION_RECEIPT_FIELDS,
        "retained cross-implementation receipt",
    )
    if rich_cross != dict(cross):
        raise ExportError(
            "retained and normalized cross-implementation receipts differ"
        )

    result: dict[str, dict[str, object]] = {}
    for oracle_name, directory_name in (
        ("dumi-off", "dumi-off"),
        ("canonical-upstream", "canonical-upstream"),
    ):
        inspection = require_object(
            strict_json(
                root / directory_name / "inspection.json",
                f"retained {oracle_name} inspection",
            ),
            f"retained {oracle_name} inspection",
        )
        require_exact_fields(
            inspection,
            SEMANTIC_INSPECTION_FIELDS,
            f"retained {oracle_name} inspection",
        )
        expected = normalized[oracle_name]
        output_records = positive_integer(
            inspection["output_records"],
            f"retained {oracle_name} output records",
        )
        reference_sequences = positive_integer(
            inspection["reference_sequences"],
            f"retained {oracle_name} reference sequences",
        )
        read_groups = nonnegative_integer(
            inspection["read_groups"],
            f"retained {oracle_name} read groups",
        )
        alignment_records = nonnegative_integer(
            inspection["alignment_group_output_records"],
            f"retained {oracle_name} alignment-group records",
        )
        excluded_second = nonnegative_integer(
            inspection["alignment_group_records_excluded_second_of_pair"],
            f"retained {oracle_name} excluded second-of-pair records",
        )
        excluded_unmapped = nonnegative_integer(
            inspection["alignment_group_records_excluded_unmapped"],
            f"retained {oracle_name} excluded unmapped records",
        )
        for field in (
            "semantic_sha256",
            "output_sha256",
            "reference_dictionary_sha256",
            "read_group_dictionary_sha256",
            "alignment_group_output_count_sha256",
        ):
            if not isinstance(inspection[field], str) or not SHA256.fullmatch(
                inspection[field]
            ):
                raise ExportError(
                    f"retained {oracle_name} inspection has an invalid hash"
                )
        expected_output_file = (
            f"oracles/external/{source_id}/{directory_name}/"
            "output.private.bam"
        )
        if (
            inspection["quickcheck"] is not True
            or inspection["quickcheck_status"] != "pass"
            or inspection["output_file"] != expected_output_file
            or output_records
            != positive_integer(
                expected["expected_output_records"],
                f"{oracle_name} normalized output records",
            )
            or inspection["semantic_sha256"]
            != expected["expected_semantic_sha256"]
            or inspection["sort_order"] != "coordinate"
            or reference_sequences
            != positive_integer(
                expected["expected_reference_sequences"],
                f"{oracle_name} normalized reference sequences",
            )
            or inspection["reference_dictionary_sha256"]
            != expected["expected_reference_dictionary_sha256"]
            or inspection["alignment_group_fingerprint_version"]
            != ALIGNMENT_GROUP_FINGERPRINT_VERSION
            or inspection["alignment_group_mode"]
            != ("paired" if workload["paired"] else "single-end")
            or alignment_records + excluded_second + excluded_unmapped
            != output_records
            or nonnegative_integer(
                inspection["output_bytes"],
                f"retained {oracle_name} output bytes",
            )
            <= 0
        ):
            raise ExportError(f"retained {oracle_name} inspection is inconsistent")
        result[oracle_name] = {
            "output_sha256": inspection["output_sha256"],
            "output_records": output_records,
            "semantic_sha256": inspection["semantic_sha256"],
            "reference_sequences": reference_sequences,
            "reference_dictionary_sha256": inspection[
                "reference_dictionary_sha256"
            ],
            "read_groups": read_groups,
            "read_group_dictionary_sha256": inspection[
                "read_group_dictionary_sha256"
            ],
            "alignment_group_output_records": alignment_records,
            "alignment_group_records_excluded_second_of_pair": excluded_second,
            "alignment_group_records_excluded_unmapped": excluded_unmapped,
            "alignment_group_output_count_sha256": inspection[
                "alignment_group_output_count_sha256"
            ],
        }

        if oracle_name == "dumi-off":
            empty_reference_contract = (
                inspection["expected_reference_sequences"] is None
                and inspection["expected_reference_dictionary_sha256"] == ""
                and inspection["expected_read_groups"] is None
                and inspection["expected_read_group_dictionary_sha256"] == ""
                and inspection[
                    "alignment_group_output_count_reused_from_exact_reference"
                ]
                is False
                and inspection["reference_file"] == ""
                and inspection["reference_file_sha256"] == ""
                and inspection["reference_canonical_sha256"] == ""
                and inspection["reference_canonical_sha256_verified"] is None
                and inspection["reference_cache_receipt_verified"] is None
                and inspection["reference_cache_receipt_sha256"] == ""
                and inspection["reference_alignment_group_output_records"] is None
                and inspection[
                    "reference_alignment_group_records_excluded_unmapped"
                ]
                is None
                and inspection[
                    "reference_alignment_group_records_excluded_second_of_pair"
                ]
                is None
                and inspection[
                    "reference_alignment_group_output_count_sha256"
                ]
                == ""
                and inspection["record_equivalent"] is None
                and inspection["reference_dictionary_equivalent"] is None
                and inspection["read_group_dictionary_equivalent"] is None
                and inspection["alignment_group_output_count_equivalent"] is None
                and inspection["exact_oracle_match"] is True
            )
            if not empty_reference_contract:
                raise ExportError(
                    "retained dUMI-off inspection has an invalid no-reference contract"
                )

    dumi = result["dumi-off"]
    canonical_path = root / "canonical-upstream" / "inspection.json"
    canonical = require_object(
        strict_json(canonical_path, "retained canonical-upstream inspection"),
        "retained canonical-upstream inspection",
    )
    for field in (
        "expected_reference_dictionary_sha256",
        "expected_read_group_dictionary_sha256",
        "reference_file_sha256",
        "reference_canonical_sha256",
        "reference_cache_receipt_sha256",
        "reference_alignment_group_output_count_sha256",
    ):
        if not isinstance(canonical[field], str) or not SHA256.fullmatch(
            canonical[field]
        ):
            raise ExportError(
                "retained canonical-upstream inspection has an invalid reference hash"
            )
    canonical_result = result["canonical-upstream"]
    reference_contract_valid = (
        canonical["expected_reference_sequences"]
        == dumi["reference_sequences"]
        and canonical["expected_reference_dictionary_sha256"]
        == dumi["reference_dictionary_sha256"]
        and canonical["expected_read_groups"] == dumi["read_groups"]
        and canonical["expected_read_group_dictionary_sha256"]
        == dumi["read_group_dictionary_sha256"]
        and canonical["reference_file"]
        == (
            f"<EVIDENCE_DIR>/oracles/external/{source_id}/"
            "dumi-off/output.private.bam"
        )
        and canonical["reference_file_sha256"] == dumi["output_sha256"]
        and canonical["reference_canonical_sha256"] == dumi["semantic_sha256"]
        and canonical["reference_canonical_sha256_verified"] is True
        and canonical["reference_cache_receipt_verified"] is True
        and canonical["reference_alignment_group_output_records"]
        == dumi["alignment_group_output_records"]
        and canonical[
            "reference_alignment_group_records_excluded_second_of_pair"
        ]
        == dumi["alignment_group_records_excluded_second_of_pair"]
        and canonical["reference_alignment_group_records_excluded_unmapped"]
        == dumi["alignment_group_records_excluded_unmapped"]
        and canonical["reference_alignment_group_output_count_sha256"]
        == dumi["alignment_group_output_count_sha256"]
        and canonical[
            "alignment_group_output_count_reused_from_exact_reference"
        ]
        is exact_match
    )
    observed = {
        "exact_match": (
            canonical_result["output_records"] == dumi["output_records"]
            and canonical_result["semantic_sha256"]
            == dumi["semantic_sha256"]
        ),
        "output_count_match": (
            canonical_result["output_records"] == dumi["output_records"]
        ),
        "alignment_group_output_record_counts_equal": (
            canonical_result["alignment_group_output_records"]
            == dumi["alignment_group_output_records"]
        ),
        "excluded_unmapped_counts_equal": (
            canonical_result["alignment_group_records_excluded_unmapped"]
            == dumi["alignment_group_records_excluded_unmapped"]
        ),
        "excluded_second_of_pair_counts_equal": (
            canonical_result[
                "alignment_group_records_excluded_second_of_pair"
            ]
            == dumi["alignment_group_records_excluded_second_of_pair"]
        ),
        "ordered_sq_equal": (
            canonical_result["reference_sequences"]
            == dumi["reference_sequences"]
            and canonical_result["reference_dictionary_sha256"]
            == dumi["reference_dictionary_sha256"]
        ),
        "ordered_rg_equal": (
            canonical_result["read_groups"] == dumi["read_groups"]
            and canonical_result["read_group_dictionary_sha256"]
            == dumi["read_group_dictionary_sha256"]
        ),
        "alignment_group_output_count_match": (
            canonical_result["alignment_group_output_records"]
            == dumi["alignment_group_output_records"]
            and canonical_result["alignment_group_output_count_sha256"]
            == dumi["alignment_group_output_count_sha256"]
        ),
    }
    observed["record_counts_equal"] = observed["output_count_match"]
    observed["alignment_group_output_count_multiset_equal"] = observed[
        "alignment_group_output_count_match"
    ]
    if (
        not reference_contract_valid
        or observed["exact_match"] is not exact_match
        or canonical["record_equivalent"] is not observed["exact_match"]
        or canonical["exact_oracle_match"] is not observed["exact_match"]
        or canonical["reference_dictionary_equivalent"]
        is not observed["ordered_sq_equal"]
        or canonical["read_group_dictionary_equivalent"]
        is not observed["ordered_rg_equal"]
        or canonical["alignment_group_output_count_equivalent"]
        is not observed["alignment_group_output_count_match"]
        or any(cross[field] is not value for field, value in observed.items())
    ):
        raise ExportError(
            "retained canonical-upstream inspection is inconsistent with dUMI-off"
        )
    return result


def validate_source_manifest(
    manifest: dict[str, object],
    bundle: Path,
    environment: Mapping[str, object],
) -> tuple[list[str], dict[str, dict[str, object]], set[str]]:
    require_exact_fields(manifest, MANIFEST_FIELDS, "restricted manifest")
    validate_restricted_environment(
        environment, manifest["subprocess_environment"]
    )
    if (
        manifest["format"] != 2
        or manifest["timing_design_version"] != 2
        or manifest["publication_profile"] != "restricted-method-auditable"
        or manifest["automatic_publication"] is not False
        or manifest["contains_source_content_hashes"] is not True
        or manifest["intermediate"] is not None
    ):
        raise ExportError("restricted manifest is not an external restricted bundle")

    config = require_object(manifest["config"], "restricted manifest config")
    require_exact_fields(config, CONFIG_FIELDS, "restricted manifest config")
    repetitions = positive_integer(config["repetitions"], "repetitions")
    active_processors = positive_integer(
        config["active_processors"], "active processor count"
    )
    if (
        config["input_mode"] != "external_bam"
        or config["timing_design_version"] != 2
        or config["include_intermediate"] is not False
        or config["keep_outputs"] is not False
        or config["allow_output_in_repo"] is not False
        or config["cluster_sort_command"] != "<GNU_SORT>"
        or config["dumi_ref"] is not None
        or config["profile"] is not None
        or config["hotspot_families"] is not None
        or config["moderate_families_per_group"] is not None
        or config["moderate_groups"] is not None
        or config["paired_pairs_per_reference"] is not None
        or config["paired_references"] != []
        or config["seed"] is not None
        or config["selected_workloads"] != []
        or config["sparse_records"] != []
        or config["cluster_tag_xmx_source"]
        not in {
            "explicit-cluster-tag-xmx",
            "inherited-from-xmx",
        }
        or (
            config["cluster_tag_xmx_source"] == "inherited-from-xmx"
            and config["cluster_tag_xmx"] != config["xmx"]
        )
    ):
        raise ExportError("restricted manifest external-mode configuration is invalid")
    source_ids_value = config["external_workload_ids"]
    if (
        not isinstance(source_ids_value, list)
        or not source_ids_value
        or not all(
            isinstance(value, str)
            and PRIVATE_SOURCE_ID.fullmatch(value)
            and len(value) <= 64
            for value in source_ids_value
        )
        or len(source_ids_value) != len(set(source_ids_value))
    ):
        raise ExportError("restricted manifest workload identifiers are invalid")
    source_ids = list(source_ids_value)

    canonical = require_object(manifest["canonical"], "canonical source")
    dumi = require_object(manifest["dumi"], "dUMI source")
    require_exact_fields(canonical, CANONICAL_FIELDS, "canonical source")
    require_exact_fields(dumi, DUMI_FIELDS, "dUMI source")
    expected_urls = (
        (canonical, "canonical source", CANONICAL_PUBLIC_URL),
        (dumi, "dUMI source", DUMI_PUBLIC_URL),
    )
    for source, context, expected_url in expected_urls:
        if (
            source["url"] != expected_url
            or not isinstance(source["sha"], str)
            or not GIT_SHA.fullmatch(source["sha"])
        ):
            raise ExportError(f"{context} provenance is invalid")
    if (
        dumi["ref"] is not None
        or dumi["ref_recorded"] is not False
        or dumi["uncommitted_worktree_sources_excluded"] is not True
        or not isinstance(dumi["worktree_was_dirty"], bool)
        or config["dumi_source_sha"] != dumi["sha"]
    ):
        raise ExportError("dUMI source provenance is invalid")
    if canonical["provenance_ref"] != "refs/heads/master":
        raise ExportError("canonical source provenance is invalid")
    if canonical["sha"] != CANONICAL_SOURCE_SHA:
        raise ExportError("canonical source provenance is invalid")

    dependencies = manifest["dependencies"]
    if not isinstance(dependencies, list):
        raise ExportError("restricted dependency inventory is invalid")
    if len(dependencies) != len(LOCKED_DEPENDENCIES):
        raise ExportError("restricted dependency inventory is incomplete")
    dependency_hashes: set[str] = set()
    dependency_names: set[str] = set()
    dependencies_by_name: dict[str, dict[str, object]] = {}
    for item, expected_dependency in zip(
        dependencies, LOCKED_DEPENDENCIES
    ):
        dependency = require_object(item, "dependency")
        require_exact_fields(dependency, DEPENDENCY_FIELDS, "dependency")
        filename = dependency["filename"]
        digest = dependency["sha256"]
        url = dependency["url"]
        expected_filename, expected_digest, expected_url = expected_dependency
        if (
            not isinstance(filename, str)
            or not SAFE_FILENAME.fullmatch(filename)
            or filename != expected_filename
            or filename in dependency_names
            or not isinstance(digest, str)
            or not SHA256.fullmatch(digest)
            or digest != expected_digest
            or digest in dependency_hashes
            or not isinstance(url, str)
            or url != expected_url
        ):
            raise ExportError("restricted dependency inventory is invalid")
        dependency_names.add(filename)
        dependency_hashes.add(digest)
        dependencies_by_name[filename] = dependency
    if set(dependencies_by_name) != set(PUBLIC_DEPENDENCY_URLS):
        raise ExportError("restricted dependency inventory is incomplete")

    dependency_files = manifest["dependency_files"]
    if (
        not isinstance(dependency_files, list)
        or len(dependency_files) != len(dependencies_by_name)
    ):
        raise ExportError("restricted dependency-file inventory is invalid")
    seen_dependency_paths: set[str] = set()
    for value in dependency_files:
        receipt = require_object(value, "dependency-file receipt")
        require_exact_fields(
            receipt, DEPENDENCY_FILE_FIELDS, "dependency-file receipt"
        )
        relative = receipt["path"]
        digest = receipt["sha256"]
        if (
            not isinstance(relative, str)
            or not relative.startswith("dependencies/")
            or PurePosixPath(relative).parts
            != ("dependencies", PurePosixPath(relative).name)
            or relative in seen_dependency_paths
            or not isinstance(digest, str)
            or not SHA256.fullmatch(digest)
        ):
            raise ExportError("restricted dependency-file inventory is invalid")
        filename = PurePosixPath(relative).name
        dependency = dependencies_by_name.get(filename)
        dependency_path = bundle / "dependencies" / filename
        if (
            dependency is None
            or dependency["sha256"] != digest
            or not dependency_path.is_file()
            or dependency_path.is_symlink()
            or sha256_file(dependency_path) != digest
        ):
            raise ExportError("restricted dependency-file checksum is inconsistent")
        seen_dependency_paths.add(relative)
    if {
        PurePosixPath(path).name for path in seen_dependency_paths
    } != set(dependencies_by_name):
        raise ExportError("restricted dependency-file inventory is incomplete")

    workloads_value = manifest["workloads"]
    if not isinstance(workloads_value, list) or not workloads_value:
        raise ExportError("restricted workload inventory is invalid")
    workloads: dict[str, dict[str, object]] = {}
    for workload_index, item in enumerate(workloads_value):
        workload = require_object(item, "restricted workload")
        require_exact_fields(workload, WORKLOAD_FIELDS, "restricted workload")
        source_id = workload["scale"]
        if (
            not isinstance(source_id, str)
            or not PRIVATE_SOURCE_ID.fullmatch(source_id)
            or len(source_id) > 64
            or source_id in workloads
        ):
            raise ExportError("restricted workload inventory is invalid")
        if (
            workload["name"] != "external"
            or workload["input_mode"] != "external_bam"
            or workload["generator_arguments"] != []
            or workload["forced_on_contract_recorded"] is not True
            or not isinstance(workload["paired"], bool)
            or not isinstance(workload["streaming_on_eligible"], bool)
            or not isinstance(workload["rationale_provided"], bool)
        ):
            raise ExportError("restricted workload contract is invalid")
        umi_length = positive_integer(workload["umi_length"], "UMI length")
        if umi_length <= 1:
            raise ExportError("restricted workload UMI length is invalid")
        separator = safe_one_line(
            workload["umi_separator"], "UMI separator", maximum=8
        )
        if (
            not SAFE_UMI_SEPARATOR.fullmatch(separator)
            or separator.startswith("-")
        ):
            raise ExportError("restricted workload UMI separator is not publishable")
        workload["umi_length"] = umi_length
        workload["umi_separator"] = separator
        treatment_count = (
            4 if workload["streaming_on_eligible"] is True else 3
        )
        schedule = require_object(
            workload["timing_stage_schedule"], "timing-stage schedule"
        )
        workload["_validated_capacity_input_bytes"] = (
            validate_capacity_receipt(
                bundle=bundle,
                source_id=source_id,
                workload=workload,
                schedule=schedule,
                treatment_count=treatment_count,
                repetitions=repetitions,
                workload_index=workload_index,
            )
        )
        directional_manifest = require_object(
            workload["directional_oracle_gate"],
            "directional-oracle manifest gate",
        )
        pairwise_manifest = require_object(
            workload["pairwise_cluster_diagnostic"],
            "pairwise cluster diagnostic",
        )
        performance_manifest = require_object(
            workload["performance_comparability"],
            "performance comparability",
        )
        validate_directional_oracle_evidence(
            bundle=bundle,
            source_id=source_id,
            workload=workload,
            directional_manifest=directional_manifest,
            pairwise_manifest=pairwise_manifest,
            performance_manifest=performance_manifest,
        )
        workloads[source_id] = workload
    if source_ids != list(workloads):
        raise ExportError("restricted workload inventories are inconsistent")

    provenance_ledger = require_object(
        manifest["external_provenance_ledger"],
        "external provenance ledger receipt",
    )
    require_exact_fields(
        provenance_ledger,
        EXTERNAL_PROVENANCE_LEDGER_FIELDS,
        "external provenance ledger receipt",
    )
    if (
        provenance_ledger["schema"]
        != EXTERNAL_PROVENANCE_LEDGER_SCHEMA
        or provenance_ledger["version"]
        != EXTERNAL_PROVENANCE_LEDGER_VERSION
        or isinstance(provenance_ledger["version"], bool)
        or not isinstance(provenance_ledger["sha256"], str)
        or not SHA256.fullmatch(provenance_ledger["sha256"])
        or provenance_ledger["workload_count"] != len(source_ids)
        or isinstance(provenance_ledger["workload_count"], bool)
        or provenance_ledger["authorization_confirmed"] is not True
        or provenance_ledger["pre_deduplication_confirmed"] is not True
        or provenance_ledger["path_recorded"] is not False
        or provenance_ledger["content_retained"] is not False
    ):
        raise ExportError("restricted external provenance ledger is invalid")

    external_inputs = manifest["external_inputs"]
    if not isinstance(external_inputs, list) or len(external_inputs) != len(source_ids):
        raise ExportError("restricted external-input receipts are inconsistent")
    require_unique_external_input_hashes(external_inputs)
    receipt_ids: list[str] = []
    for value in external_inputs:
        receipt = require_object(value, "external-input receipt")
        require_exact_fields(
            receipt, EXTERNAL_INPUT_FIELDS, "external-input receipt"
        )
        source_id = receipt["workload_id"]
        workload = workloads.get(source_id) if isinstance(source_id, str) else None
        input_provenance_ledger = require_object(
            receipt["provenance_ledger"],
            "external-input provenance ledger receipt",
        )
        if input_provenance_ledger != provenance_ledger:
            raise ExportError(
                "restricted external-input provenance ledger is inconsistent"
            )
        if (
            workload is None
            or receipt["path_recorded"] is not False
            or receipt["quickcheck_status"] != "pass"
            or receipt["temporary_index_validation"] != "pass"
            or receipt["alias_neutrality_machine_verified"] is not False
            or receipt["declared_sort_order"] != "coordinate"
            or receipt["paired"] is not workload["paired"]
            or receipt["umi_length"] != workload["umi_length"]
            or receipt["umi_separator"] != workload["umi_separator"]
            or receipt["rationale_provided"]
            is not workload["rationale_provided"]
            or not isinstance(receipt["sha256"], str)
            or not SHA256.fullmatch(receipt["sha256"])
            or not isinstance(receipt["reference_dictionary_sha256"], str)
            or not SHA256.fullmatch(
                receipt["reference_dictionary_sha256"]
            )
        ):
            raise ExportError("restricted external-input receipt did not pass")
        input_bytes = positive_integer(
            receipt["bytes"], "external input bytes"
        )
        total_records = positive_integer(
            receipt["total_records"], "external total records"
        )
        mapped_records = positive_integer(
            receipt["mapped_records"], "external mapped records"
        )
        paired_records = nonnegative_integer(
            receipt["paired_records"], "external paired records"
        )
        qnames_checked = positive_integer(
            receipt["qnames_checked"], "external checked QNAMEs"
        )
        positive_integer(
            receipt["reference_sequences"], "external reference sequences"
        )
        directional_source_metrics = require_object(
            workload["_validated_directional_source_metrics"],
            "validated directional source metrics",
        )
        expected_qnames = (
            directional_source_metrics["eligible_records"]
            + directional_source_metrics["excluded_mate_unmapped"]
            if workload["paired"] is True
            else directional_source_metrics["eligible_records"]
        )
        if (
            mapped_records > total_records
            or qnames_checked > mapped_records
            or qnames_checked != expected_qnames
            or (
                paired_records != total_records
                if workload["paired"]
                else paired_records != 0
            )
            or input_bytes != workload["_validated_capacity_input_bytes"]
            or input_bytes
            != workload["_validated_directional_input_bytes"]
            or total_records
            != workload["_validated_capacity_record_count"]
            or total_records
            != workload["_validated_directional_input_records"]
            or receipt["sha256"]
            != workload["_validated_directional_input_sha256"]
            or receipt["reference_sequences"]
            != workload["_validated_directional_reference_sequences"]
            or receipt["reference_dictionary_sha256"]
            != workload[
                "_validated_directional_reference_dictionary_sha256"
            ]
        ):
            raise ExportError("restricted external-input receipt is inconsistent")
        snapshot = require_object(
            receipt["private_timing_snapshot"],
            "private timing-snapshot receipt",
        )
        require_exact_fields(
            snapshot,
            PRIVATE_SNAPSHOT_FIELDS,
            "private timing-snapshot receipt",
        )
        if (
            snapshot["kind"] != "verified_private_copy"
            or snapshot["bytes"] != input_bytes
            or snapshot["sha256"] != receipt["sha256"]
            or snapshot["read_only"] is not True
            or snapshot["path_recorded"] is not False
            or snapshot["retained_after_sealing"] is not False
        ):
            raise ExportError("restricted private timing snapshot is invalid")
        timing_index = require_object(
            snapshot["timing_index"], "private timing-index receipt"
        )
        require_exact_fields(
            timing_index,
            PRIVATE_PAIRED_INDEX_FIELDS,
            "private timing-index receipt",
        )
        if (
            positive_integer(
                timing_index["bytes"], "private timing-index bytes"
            )
            <= 0
            or not isinstance(timing_index["sha256"], str)
            or not SHA256.fullmatch(timing_index["sha256"])
            or timing_index["format"] not in {"bai", "csi"}
            or timing_index["path_recorded"] is not False
        ):
            raise ExportError("restricted private timing index is invalid")
        source_index = receipt["paired_index"]
        snapshot_index = snapshot["paired_index"]
        if workload["paired"]:
            source_index_object = require_object(
                source_index, "paired-index receipt"
            )
            snapshot_index_object = require_object(
                snapshot_index, "private paired-index receipt"
            )
            require_exact_fields(
                source_index_object,
                PAIRED_INDEX_FIELDS,
                "paired-index receipt",
            )
            require_exact_fields(
                snapshot_index_object,
                PRIVATE_PAIRED_INDEX_FIELDS,
                "private paired-index receipt",
            )
            if (
                positive_integer(
                    source_index_object["bytes"], "paired-index bytes"
                )
                != positive_integer(
                    snapshot_index_object["bytes"],
                    "private paired-index bytes",
                )
                or source_index_object["sha256"]
                != snapshot_index_object["sha256"]
                or not isinstance(source_index_object["sha256"], str)
                or not SHA256.fullmatch(source_index_object["sha256"])
                or source_index_object["path_recorded"] is not False
                or source_index_object["validation"] != "pass"
                or snapshot_index_object["path_recorded"] is not False
                or snapshot_index_object["format"] not in {"bai", "csi"}
                or timing_index != snapshot_index_object
            ):
                raise ExportError("restricted paired-index receipt is invalid")
        elif source_index is not None or snapshot_index is not None:
            raise ExportError("restricted single-end input has an index receipt")
        validate_forced_on_contract(
            receipt["forced_on_contract"],
            eligible=workload["streaming_on_eligible"] is True,
            paired=workload["paired"] is True,
        )
        workload["_validated_input_sha256"] = receipt["sha256"]
        workload["_validated_reference_sequences"] = receipt[
            "reference_sequences"
        ]
        workload["_validated_reference_dictionary_sha256"] = receipt[
            "reference_dictionary_sha256"
        ]
        receipt_ids.append(source_id)
    if receipt_ids != source_ids:
        raise ExportError("restricted external-input receipts are inconsistent")
    for source_id in source_ids:
        evidence, exact_match = validate_oracle_receipts(
            bundle=bundle,
            source_id=source_id,
            workload=workloads[source_id],
            canonical_sha=str(canonical["sha"]),
            dumi_sha=str(dumi["sha"]),
        )
        workloads[source_id]["_validated_oracle_evidence"] = evidence
        workloads[source_id]["_validated_cross_exact"] = exact_match

    implementations = require_object(
        manifest["implementation_sources"], "implementation sources"
    )
    expected_implementations = {
        "canonical-upstream/legacy": canonical["sha"],
        "dumi/off": dumi["sha"],
        "dumi/on": dumi["sha"],
        "dumi/auto": dumi["sha"],
    }
    if implementations != expected_implementations:
        raise ExportError("implementation source inventory is invalid")

    for field in ("xms", "xmx", "cluster_tag_xmx"):
        value = safe_one_line(config[field], f"benchmark {field}", maximum=32)
        if not SAFE_MEMORY_SIZE.fullmatch(value):
            raise ExportError(f"benchmark {field} is invalid")
        config[field] = value
    expected_jvm_options = [
        "-XX:-UsePerfData",
        "-server",
        f"-Xms{config['xms']}",
        f"-Xmx{config['xmx']}",
        "-Xss20m",
        f"-XX:ActiveProcessorCount={active_processors}",
    ]
    expected_cluster_options = [
        option
        for option in expected_jvm_options
        if not option.startswith("-Xmx")
    ] + [f"-Xmx{config['cluster_tag_xmx']}"]
    if (
        manifest["jvm_options"] != expected_jvm_options
        or manifest["cluster_tag_jvm_options"] != expected_cluster_options
    ):
        raise ExportError("restricted JVM option vectors are invalid")
    for workload in workloads.values():
        workload["_validated_jvm_options"] = tuple(expected_jvm_options)

    public_identity_hashes = dependency_hashes | validate_method_identity(
        manifest,
        bundle,
        environment,
        list(dependencies_by_name.values()),
    )
    config["_validated_repetitions"] = repetitions
    config["_validated_active_processors"] = active_processors
    return source_ids, workloads, public_identity_hashes


def validate_aliases(
    aliases: dict[str, str],
    source_ids: list[str],
    workloads: Mapping[str, Mapping[str, object]],
) -> None:
    if set(aliases) != set(source_ids):
        raise ExportError("private alias map is not complete for this bundle")
    for source_id in source_ids:
        public = aliases[source_id]
        paired = workloads[source_id]["paired"]
        expected_marker = "-pe-" if paired else "-se-"
        if expected_marker not in public:
            raise ExportError("public alias does not match the workload pairing mode")


def validate_cell(row: Mapping[str, str], context: str) -> None:
    if row["workload"] != "external":
        raise ExportError(f"{context} contains a non-external workload")
    implementation = row["implementation"]
    mode = row["mode"]
    if implementation not in SAFE_IMPLEMENTATIONS or mode not in SAFE_MODES:
        raise ExportError(f"{context} contains an unsupported implementation")
    if (
        (implementation == "canonical-upstream" and mode != "legacy")
        or (implementation == "dumi" and mode == "legacy")
    ):
        raise ExportError(f"{context} contains an invalid implementation mode")


def public_run_id(row: Mapping[str, str], alias: str) -> str:
    repetition = positive_integer(row["repetition"], "repetition")
    order = positive_integer(row["order"], "order")
    return (
        f"{alias}-r{repetition:03d}-o{order:02d}-"
        f"{row['implementation']}-{row['mode']}-{row['stage']}"
    )


def transform_design(
    rows: list[dict[str, str]], aliases: Mapping[str, str]
) -> list[dict[str, object]]:
    public: list[dict[str, object]] = []
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        validate_cell(row, "restricted design")
        if row["scale"] not in aliases or row["stage"] not in SAFE_STAGES:
            raise ExportError("restricted design contains an unknown workload or stage")
        key = tuple(row[field] for field in DESIGN_INPUT_FIELDS)
        if key in seen:
            raise ExportError("restricted design contains a duplicate row")
        seen.add(key)
        alias = aliases[row["scale"]]
        public.append(
            {
                "run_id": public_run_id(row, alias),
                "workload_id": alias,
                "stage": row["stage"],
                "implementation": row["implementation"],
                "mode": row["mode"],
                "repetition": positive_integer(row["repetition"], "repetition"),
                "order": positive_integer(row["order"], "order"),
            }
        )
    return public


def transform_measurements(
    rows: list[dict[str, str]],
    aliases: Mapping[str, str],
    workloads: Mapping[str, Mapping[str, object]],
    *,
    bundle: Path,
    redacted_paths: set[str],
) -> list[dict[str, object]]:
    public: list[dict[str, object]] = []
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        validate_cell(row, "restricted measurements")
        if row["scale"] not in aliases or row["stage"] not in SAFE_STAGES:
            raise ExportError(
                "restricted measurements contain an unknown workload or stage"
            )
        workload = workloads[row["scale"]]
        design_key = tuple(row[field] for field in DESIGN_INPUT_FIELDS)
        if design_key in seen:
            raise ExportError("restricted measurements contain a duplicate run")
        seen.add(design_key)
        if nonnegative_integer(row["exit_code"], "exit code") != 0:
            raise ExportError("restricted measurements contain a failed run")
        for field in (
            "input_sha256",
            "semantic_sha256",
            "output_sha256",
            "reference_dictionary_sha256",
            "expected_semantic_sha256",
            "expected_reference_dictionary_sha256",
        ):
            if not SHA256.fullmatch(row[field]):
                raise ExportError("restricted measurements contain an invalid hash")
        output_records = nonnegative_integer(
            row["output_records"], "output record count"
        )
        expected_output_records = nonnegative_integer(
            row["expected_output_records"], "expected output record count"
        )
        reference_sequences = positive_integer(
            row["reference_sequences"], "reference sequence count"
        )
        expected_reference_sequences = positive_integer(
            row["expected_reference_sequences"],
            "expected reference sequence count",
        )
        if (
            output_records != expected_output_records
            or row["semantic_sha256"] != row["expected_semantic_sha256"]
            or reference_sequences != expected_reference_sequences
            or row["reference_dictionary_sha256"]
            != row["expected_reference_dictionary_sha256"]
        ):
            raise ExportError("restricted measurements contain an oracle mismatch")
        if (
            row["sort_order"] not in {"coordinate", "unsorted"}
            or (
                row["stage"] == "end_to_end_ready"
                and row["sort_order"] != "coordinate"
            )
        ):
            raise ExportError("restricted measurements contain an invalid sort order")
        exact = strict_boolean(row["exact_oracle_match"], "exact oracle match")
        cross_exact = strict_boolean(
            row["cross_implementation_exact_match"],
            "cross-implementation exact match",
        )
        cross_output_count = strict_boolean(
            row["cross_implementation_output_count_match"],
            "cross-implementation output-count match",
        )
        cross_groups = strict_boolean(
            row["cross_implementation_alignment_group_output_count_match"],
            "cross-implementation alignment-group match",
        )
        directional_gate = strict_boolean(
            row["directional_oracle_gate_pass"],
            "directional-oracle gate",
        )
        dumi_partition = strict_boolean(
            row["dumi_off_oracle_partition_equivalent"],
            "dUMI-off directional partition equivalence",
        )
        dumi_root = strict_boolean(
            row["dumi_off_oracle_root_assignment_equivalent"],
            "dUMI-off directional root equivalence",
        )
        diagnostics = {
            field: strict_boolean(
                row[field], field.replace("_", " ")
            )
            for field in (
                "canonical_upstream_oracle_partition_equivalent",
                "canonical_upstream_oracle_root_assignment_equivalent",
                "canonical_upstream_dumi_off_partition_equivalent",
                "canonical_upstream_dumi_off_root_assignment_equivalent",
            )
        }
        expected_gate = workload["_validated_directional_gate"]
        expected_diagnostics = workload[
            "_validated_directional_diagnostics"
        ]
        if (
            not exact
            or not directional_gate
            or not dumi_partition
            or not dumi_root
            or cross_exact is not workload["_validated_cross_exact"]
            or cross_output_count
            is not workload["_validated_cross_output_count"]
            or cross_groups
            is not workload["_validated_cross_alignment_group"]
            or directional_gate
            is not expected_gate["directional_oracle_gate_pass"]
            or dumi_partition
            is not expected_gate["dumi_off_oracle_partition_equivalent"]
            or dumi_root
            is not expected_gate[
                "dumi_off_oracle_root_assignment_equivalent"
            ]
            or any(
                diagnostics[field] is not expected_diagnostics[field]
                for field in diagnostics
            )
        ):
            raise ExportError("restricted measurements contain failed correctness")
        route = row["actual_route"]
        if row["implementation"] == "canonical-upstream":
            allowed_routes = {"coordinate"}
            expected_oracle = "canonical-upstream"
        elif row["mode"] == "off":
            allowed_routes = {"off"}
            expected_oracle = "dumi-off"
        elif row["mode"] == "on":
            allowed_routes = (
                {"streaming"}
                if workload["streaming_on_eligible"] is True
                else set()
            )
            expected_oracle = "dumi-off"
        else:
            allowed_routes = (
                {"streaming", "fallback-off"}
                if workload["streaming_on_eligible"] is True
                else {"off-ineligible", "fallback-off"}
            )
            expected_oracle = "dumi-off"
        if (
            route not in SAFE_ROUTES
            or route not in allowed_routes
            or row["oracle_implementation"] != expected_oracle
            or row["directional_oracle_receipt"]
            != workload["_validated_directional_receipt"]
        ):
            raise ExportError("restricted measurements contain an unsafe route")
        expected_sort_order = (
            "unsorted"
            if row["stage"] == "raw" and route == "streaming"
            else "coordinate"
        )
        if row["sort_order"] != expected_sort_order:
            raise ExportError(
                "restricted measurement route and sort order are inconsistent"
            )
        validate_per_run_receipts(
            bundle=bundle,
            row=row,
            workload=workload,
            redacted_paths=redacted_paths,
        )
        alias = aliases[row["scale"]]
        public.append(
            {
                "run_id": public_run_id(row, alias),
                "workload_id": alias,
                "stage": row["stage"],
                "implementation": row["implementation"],
                "mode": row["mode"],
                "repetition": positive_integer(row["repetition"], "repetition"),
                "order": positive_integer(row["order"], "order"),
                "exit_code": 0,
                "elapsed_s": finite_decimal(row["elapsed_s"], "elapsed time"),
                "user_s": finite_decimal(row["user_s"], "user time"),
                "system_s": finite_decimal(row["system_s"], "system time"),
                "cpu_pct": finite_decimal(
                    row["cpu_pct"], "CPU percentage", percent=True
                ),
                "max_rss_kib": nonnegative_integer(
                    row["max_rss_kib"], "maximum RSS"
                ),
                "actual_route": route,
                "exact_oracle_match": str(exact).lower(),
                "cross_implementation_exact_match": str(cross_exact).lower(),
                "cross_implementation_output_count_match": str(
                    cross_output_count
                ).lower(),
                "cross_implementation_alignment_group_output_count_match": str(
                    cross_groups
                ).lower(),
                "directional_oracle_gate_pass": str(
                    directional_gate
                ).lower(),
                "dumi_off_oracle_partition_equivalent": str(
                    dumi_partition
                ).lower(),
                "dumi_off_oracle_root_assignment_equivalent": str(
                    dumi_root
                ).lower(),
                **{
                    field: str(value).lower()
                    for field, value in diagnostics.items()
                },
            }
        )
    return public


def transform_summary(
    rows: list[dict[str, str]],
    aliases: Mapping[str, str],
    workloads: Mapping[str, Mapping[str, object]],
    repetitions: int,
) -> list[dict[str, object]]:
    public: list[dict[str, object]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        validate_cell(row, "restricted summary")
        if row["scale"] not in aliases or row["stage"] not in SAFE_STAGES:
            raise ExportError("restricted summary contains an unknown workload or stage")
        key = (
            row["scale"],
            row["stage"],
            row["implementation"],
            row["mode"],
        )
        if key in seen:
            raise ExportError("restricted summary contains a duplicate cell")
        seen.add(key)
        attempts = positive_integer(row["attempts"], "summary attempts")
        successful = positive_integer(
            row["successful_repetitions"], "successful repetitions"
        )
        failed = nonnegative_integer(
            row["failed_repetitions"], "failed repetitions"
        )
        comparable = workloads[row["scale"]][
            "_validated_cross_output_count"
        ]
        expected_status = "comparable" if comparable else "not_comparable"
        expected_issues = (
            "" if comparable else NONCOMPARABLE_OUTPUT_COUNT_ISSUE
        )
        if (
            row["correctness_status"] != "pass"
            or attempts != repetitions
            or successful != attempts
            or failed != 0
            or row["comparability_status"] != expected_status
            or row["comparability_issues"] != expected_issues
        ):
            raise ExportError("restricted summary contains failed correctness")
        output: dict[str, object] = {
            "workload_id": aliases[row["scale"]],
            "stage": row["stage"],
            "implementation": row["implementation"],
            "mode": row["mode"],
            "attempts": attempts,
            "successful_repetitions": successful,
            "failed_repetitions": failed,
            "correctness_status": "pass",
            "comparability_status": expected_status,
            "comparability_issues": expected_issues,
        }
        for metric in METRICS:
            n_field = f"{metric}_n"
            n_value = positive_integer(row[n_field], n_field)
            if n_value != successful:
                raise ExportError("restricted summary metric N is inconsistent")
            output[n_field] = n_value
            for statistic in ("median", "min", "max", "range", "mad"):
                field = f"{metric}_{statistic}"
                output[field] = finite_decimal(row[field], field)
        public.append(output)
    return public


def transform_comparisons(
    rows: list[dict[str, str]],
    aliases: Mapping[str, str],
    workloads: Mapping[str, Mapping[str, object]],
    repetitions: int,
) -> list[dict[str, object]]:
    public: list[dict[str, object]] = []
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        validate_cell(row, "restricted comparisons")
        if (
            row["scale"] not in aliases
            or row["stage"] not in SAFE_COMPARISON_STAGES
            or row["baseline_implementation"] != "canonical-upstream"
            or row["baseline_mode"] != "legacy"
        ):
            raise ExportError("restricted comparisons contain an invalid cell")
        key = (
            row["scale"],
            row["stage"],
            row["implementation"],
            row["mode"],
        )
        if key in seen:
            raise ExportError("restricted comparisons contain a duplicate cell")
        seen.add(key)
        attempted = positive_integer(row["attempted_pairs"], "attempted pairs")
        successful = nonnegative_integer(
            row["successful_pairs"], "successful pairs"
        )
        failed = nonnegative_integer(row["failed_pairs"], "failed pairs")
        noncomparable = nonnegative_integer(
            row["noncomparable_pairs"], "noncomparable pairs"
        )
        workload = workloads[row["scale"]]
        comparable = workload["_validated_cross_output_count"]
        expected_successful = attempted if comparable else 0
        expected_noncomparable = 0 if comparable else attempted
        expected_status = "comparable" if comparable else "not_comparable"
        expected_issues = (
            "" if comparable else NONCOMPARABLE_OUTPUT_COUNT_ISSUE
        )
        if (
            row["correctness_status"] != "pass"
            or row["issues"] != ""
            or attempted != repetitions
            or successful != expected_successful
            or failed != 0
            or noncomparable != expected_noncomparable
            or row["comparability_status"] != expected_status
            or row["comparability_issues"] != expected_issues
        ):
            raise ExportError("restricted comparisons contain failed correctness")
        output: dict[str, object] = {
            "workload_id": aliases[row["scale"]],
            "stage": row["stage"],
            "baseline_implementation": "canonical-upstream",
            "baseline_mode": "legacy",
            "implementation": row["implementation"],
            "mode": row["mode"],
            "attempted_pairs": attempted,
            "successful_pairs": successful,
            "failed_pairs": failed,
            "noncomparable_pairs": noncomparable,
            "correctness_status": "pass",
            "comparability_status": expected_status,
            "comparability_issues": expected_issues,
            "cross_implementation_exact_match": str(
                workload["_validated_cross_exact"]
            ).lower(),
            "cross_implementation_bounded_diagnostic_match": str(
                workload["_validated_cross_bounded_diagnostic"]
            ).lower(),
        }
        for metric in COMPARISON_METRICS:
            n_field = f"{metric}_n"
            n_value = nonnegative_integer(row[n_field], n_field)
            if n_value != successful:
                raise ExportError("restricted comparison metric N is inconsistent")
            output[n_field] = n_value
            for statistic in ("median", "min", "max", "range", "mad"):
                field = f"{metric}_{statistic}"
                if successful == 0:
                    if row[field] != "":
                        raise ExportError(
                            "noncomparable comparison has a populated metric"
                        )
                    output[field] = ""
                else:
                    output[field] = finite_decimal(
                        row[field], field, nonnegative=False
                    )
        public.append(output)
    return public


def transform_correctness(
    rows: list[dict[str, str]],
    aliases: Mapping[str, str],
    workloads: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    public: list[dict[str, object]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        validate_cell(row, "restricted correctness")
        if row["scale"] not in aliases or row["stage"] not in SAFE_STAGES:
            raise ExportError(
                "restricted correctness contains an unknown workload or stage"
            )
        key = (
            row["scale"],
            row["stage"],
            row["implementation"],
            row["mode"],
        )
        if key in seen:
            raise ExportError("restricted correctness contains a duplicate cell")
        seen.add(key)
        gate = workloads[row["scale"]]["_validated_directional_gate"]
        diagnostics = workloads[row["scale"]][
            "_validated_directional_diagnostics"
        ]
        observed_gate = {
            field: strict_boolean(row[field], field.replace("_", " "))
            for field in (
                "directional_oracle_gate_pass",
                "dumi_off_oracle_partition_equivalent",
                "dumi_off_oracle_root_assignment_equivalent",
            )
        }
        observed_diagnostics = {
            field: strict_boolean(row[field], field.replace("_", " "))
            for field in (
                "canonical_upstream_oracle_partition_equivalent",
                "canonical_upstream_oracle_root_assignment_equivalent",
                "canonical_upstream_dumi_off_partition_equivalent",
                "canonical_upstream_dumi_off_root_assignment_equivalent",
            )
        }
        issue_count = nonnegative_integer(row["issue_count"], "correctness issues")
        if (
            row["correctness_status"] != "pass"
            or any(
                observed_gate[field] is not gate[field]
                for field in observed_gate
            )
            or any(
                observed_diagnostics[field] is not diagnostics[field]
                for field in observed_diagnostics
            )
            or gate["directional_oracle_gate_pass"] is not True
            or issue_count != 0
            or row["issues"] != ""
            or not row["directional_oracle_receipt"]
            or row["directional_oracle_receipt"]
            != workloads[row["scale"]]["_validated_directional_receipt"]
        ):
            raise ExportError("restricted correctness contains a failed cell")
        workload = workloads[row["scale"]]
        pairwise = workload["_validated_pairwise_diagnostic"]
        public.append(
            {
                "workload_id": aliases[row["scale"]],
                "stage": row["stage"],
                "implementation": row["implementation"],
                "mode": row["mode"],
                "correctness_status": "pass",
                "cross_implementation_exact_match": str(
                    workload["_validated_cross_exact"]
                ).lower(),
                "cross_implementation_output_count_match": str(
                    workload["_validated_cross_output_count"]
                ).lower(),
                "cross_implementation_alignment_group_output_count_match": str(
                    workload["_validated_cross_alignment_group"]
                ).lower(),
                "cross_implementation_bounded_diagnostic_match": str(
                    workload["_validated_cross_bounded_diagnostic"]
                ).lower(),
                **{
                    field: str(gate[field]).lower()
                    for field in DIRECTIONAL_GATE_FIELDS
                },
                **{
                    field: str(diagnostics[field]).lower()
                    for field in DIRECTIONAL_DIAGNOSTIC_FIELDS
                },
                "upstream_agreement_required": "false",
                "pairwise_cluster_diagnostic_equivalent": str(
                    pairwise["equivalent"]
                ).lower(),
                "pairwise_cluster_partition_equivalent": str(
                    pairwise["partition_equivalent"]
                ).lower(),
                "pairwise_reference_dictionary_equivalent": str(
                    pairwise["reference_dictionary_equivalent"]
                ).lower(),
                "pairwise_read_group_dictionary_equivalent": str(
                    pairwise["read_group_dictionary_equivalent"]
                ).lower(),
                "issue_count": 0,
            }
        )
    return public


def recompute_summary(
    measurements: list[dict[str, str]],
    aliases: Mapping[str, str],
    workloads: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    grouped: dict[
        tuple[str, str, str, str], list[dict[str, str]]
    ] = defaultdict(list)
    for row in measurements:
        grouped[
            (
                row["scale"],
                row["stage"],
                row["implementation"],
                row["mode"],
            )
        ].append(row)

    public: list[dict[str, object]] = []
    for key in sorted(grouped):
        rows = grouped[key]
        output: dict[str, object] = {
            "workload_id": aliases[key[0]],
            "stage": key[1],
            "implementation": key[2],
            "mode": key[3],
            "attempts": len(rows),
            "successful_repetitions": len(rows),
            "failed_repetitions": 0,
            "correctness_status": "pass",
            "comparability_status": (
                "comparable"
                if workloads[key[0]]["_validated_cross_output_count"]
                else "not_comparable"
            ),
            "comparability_issues": (
                ""
                if workloads[key[0]]["_validated_cross_output_count"]
                else NONCOMPARABLE_OUTPUT_COUNT_ISSUE
            ),
        }
        for metric in METRICS:
            values = [
                decimal_number(
                    row[metric],
                    metric,
                    percent=(metric == "cpu_pct"),
                )
                for row in rows
            ]
            output[f"{metric}_n"] = len(values)
            for statistic, value in decimal_statistics(values).items():
                output[f"{metric}_{statistic}"] = format_decimal(value)
        public.append(output)
    return public


def require_comparable_external_pair(
    baseline: Mapping[str, str],
    treatment: Mapping[str, str],
    dumi_off: Mapping[str, str],
) -> None:
    shared_fields = (
        "input_sha256",
        "reference_sequences",
        "reference_dictionary_sha256",
        "expected_reference_sequences",
        "expected_reference_dictionary_sha256",
        "directional_oracle_gate_pass",
        "dumi_off_oracle_partition_equivalent",
        "dumi_off_oracle_root_assignment_equivalent",
        "canonical_upstream_oracle_partition_equivalent",
        "canonical_upstream_oracle_root_assignment_equivalent",
        "canonical_upstream_dumi_off_partition_equivalent",
        "canonical_upstream_dumi_off_root_assignment_equivalent",
        "directional_oracle_receipt",
    )
    if any(baseline[field] != treatment[field] for field in shared_fields):
        raise ExportError("restricted comparison source evidence is inconsistent")
    dumi_oracle_fields = (
        "expected_output_records",
        "expected_semantic_sha256",
        "expected_reference_sequences",
        "expected_reference_dictionary_sha256",
    )
    if any(treatment[field] != dumi_off[field] for field in dumi_oracle_fields):
        raise ExportError("restricted dUMI comparison oracle is inconsistent")


def recompute_comparisons(
    measurements: list[dict[str, str]],
    aliases: Mapping[str, str],
    workloads: Mapping[str, Mapping[str, object]],
    repetitions: int,
) -> list[dict[str, object]]:
    indexed: dict[
        tuple[str, str, str, str, int], dict[str, str]
    ] = {}
    for row in measurements:
        key = (
            row["scale"],
            row["stage"],
            row["implementation"],
            row["mode"],
            positive_integer(row["repetition"], "measurement repetition"),
        )
        if key in indexed:
            raise ExportError("restricted measurements repeat a comparison cell")
        indexed[key] = row

    public: list[dict[str, object]] = []
    for source_id, workload in workloads.items():
        candidates: list[tuple[str, str]] = [("dumi", "off")]
        if workload["streaming_on_eligible"] is True:
            candidates.append(("dumi", "on"))
        candidates.append(("dumi", "auto"))
        for implementation, mode in candidates:
            for stage in ("raw", "end_to_end_ready"):
                comparable = workload["_validated_cross_output_count"]
                metric_values: dict[str, list[Decimal]] = {
                    metric: [] for metric in COMPARISON_METRICS
                }
                for repetition in range(1, repetitions + 1):
                    baseline_elapsed = Decimal(0)
                    treatment_elapsed = Decimal(0)
                    baseline_rss_values: list[Decimal] = []
                    treatment_rss_values: list[Decimal] = []
                    for source_stage in (stage,):
                        baseline = indexed[
                            (
                                source_id,
                                source_stage,
                                "canonical-upstream",
                                "legacy",
                                repetition,
                            )
                        ]
                        treatment = indexed[
                            (
                                source_id,
                                source_stage,
                                implementation,
                                mode,
                                repetition,
                            )
                        ]
                        dumi_off = indexed[
                            (
                                source_id,
                                source_stage,
                                "dumi",
                                "off",
                                repetition,
                            )
                        ]
                        require_comparable_external_pair(
                            baseline, treatment, dumi_off
                        )
                        if not comparable:
                            continue
                        baseline_elapsed += decimal_number(
                            baseline["elapsed_s"], "baseline elapsed time"
                        )
                        treatment_elapsed += decimal_number(
                            treatment["elapsed_s"], "treatment elapsed time"
                        )
                        baseline_rss_values.append(
                            decimal_number(
                                baseline["max_rss_kib"],
                                "baseline maximum RSS",
                            )
                        )
                        treatment_rss_values.append(
                            decimal_number(
                                treatment["max_rss_kib"],
                                "treatment maximum RSS",
                            )
                        )
                    if not comparable:
                        continue
                    baseline_rss = max(baseline_rss_values)
                    treatment_rss = max(treatment_rss_values)
                    if (
                        baseline_elapsed <= 0
                        or treatment_elapsed <= 0
                        or baseline_rss <= 0
                    ):
                        raise ExportError(
                            "restricted comparison contains nonpositive metrics"
                        )
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
                        (baseline_rss - treatment_rss)
                        * hundred
                        / baseline_rss
                    )

                output: dict[str, object] = {
                    "workload_id": aliases[source_id],
                    "stage": stage,
                    "baseline_implementation": "canonical-upstream",
                    "baseline_mode": "legacy",
                    "implementation": implementation,
                    "mode": mode,
                    "attempted_pairs": repetitions,
                    "successful_pairs": repetitions if comparable else 0,
                    "failed_pairs": 0,
                    "noncomparable_pairs": 0 if comparable else repetitions,
                    "correctness_status": "pass",
                    "comparability_status": (
                        "comparable" if comparable else "not_comparable"
                    ),
                    "comparability_issues": (
                        ""
                        if comparable
                        else NONCOMPARABLE_OUTPUT_COUNT_ISSUE
                    ),
                    "cross_implementation_exact_match": str(
                        workload["_validated_cross_exact"]
                    ).lower(),
                    "cross_implementation_bounded_diagnostic_match": str(
                        workload["_validated_cross_bounded_diagnostic"]
                    ).lower(),
                }
                for metric in COMPARISON_METRICS:
                    values = metric_values[metric]
                    output[f"{metric}_n"] = len(values)
                    if values:
                        for statistic, value in decimal_statistics(values).items():
                            output[f"{metric}_{statistic}"] = format_decimal(
                                value
                            )
                    else:
                        for statistic in (
                            "median",
                            "min",
                            "max",
                            "range",
                            "mad",
                        ):
                            output[f"{metric}_{statistic}"] = ""
                public.append(output)
    return public


def require_same_projection(
    source: list[dict[str, object]],
    recomputed: list[dict[str, object]],
    key_fields: tuple[str, ...],
    context: str,
) -> None:
    def indexed(
        rows: list[dict[str, object]],
    ) -> dict[tuple[object, ...], dict[str, object]]:
        output: dict[tuple[object, ...], dict[str, object]] = {}
        for row in rows:
            key = tuple(row[field] for field in key_fields)
            if key in output:
                raise ExportError(f"{context} contains a duplicate public cell")
            output[key] = row
        return output

    if indexed(source) != indexed(recomputed):
        raise ExportError(f"restricted {context} does not match measurements")


def expected_balanced_order(
    treatments: tuple[tuple[str, str], ...],
    row: int,
) -> tuple[tuple[str, str], ...]:
    size = len(treatments)
    if size % 2 == 0:
        indexes = [0]
        for position in range(1, size):
            indexes.append(
                (position + 1) // 2
                if position % 2
                else size - position // 2
            )
        shift = row % size
        return tuple(
            treatments[(index + shift) % size] for index in indexes
        )
    block = row // size
    shift = row % size
    base = list(treatments)
    if block % 2:
        base.reverse()
    return tuple(base[shift:] + base[:shift])


def require_consistent_tables(
    source_ids: list[str],
    workloads: Mapping[str, Mapping[str, object]],
    design: list[dict[str, str]],
    measurements: list[dict[str, str]],
    summary: list[dict[str, str]],
    comparisons: list[dict[str, str]],
    correctness: list[dict[str, str]],
    repetitions: int,
) -> None:
    expected_ids = set(source_ids)
    for rows, context in (
        (design, "design"),
        (measurements, "measurements"),
        (summary, "summary"),
        (comparisons, "comparisons"),
        (correctness, "correctness"),
    ):
        if {row["scale"] for row in rows} != expected_ids:
            raise ExportError(f"restricted {context} workload inventory is incomplete")

    design_keys = {
        tuple(row[field] for field in DESIGN_INPUT_FIELDS) for row in design
    }
    measurement_keys = {
        tuple(row[field] for field in DESIGN_INPUT_FIELDS) for row in measurements
    }
    if (
        len(design_keys) != len(design)
        or len(measurement_keys) != len(measurements)
        or design_keys != measurement_keys
    ):
        raise ExportError("restricted design and measurements are inconsistent")
    if len({row["run_id"] for row in design}) != len(design):
        raise ExportError("restricted design contains duplicate run identifiers")
    logical_schedule: set[tuple[str, ...]] = set()
    cell_repetitions: dict[tuple[str, ...], set[int]] = defaultdict(set)
    for row in design:
        repetition = positive_integer(row["repetition"], "design repetition")
        order = positive_integer(row["order"], "design order")
        if repetition > repetitions:
            raise ExportError("restricted design repetition is outside the schedule")
        schedule_key = (
            row["scale"],
            row["stage"],
            row["implementation"],
            row["mode"],
            str(repetition),
            str(order),
        )
        if schedule_key in logical_schedule:
            raise ExportError("restricted design contains a duplicate scheduled cell")
        logical_schedule.add(schedule_key)
        cell_repetitions[
            (
                row["scale"],
                row["stage"],
                row["implementation"],
                row["mode"],
            )
        ].add(repetition)
    expected_repetitions = set(range(1, repetitions + 1))
    if any(
        observed != expected_repetitions
        for observed in cell_repetitions.values()
    ):
        raise ExportError("restricted design repetition coverage is incomplete")

    measurement_cells = {
        (row["scale"], row["stage"], row["implementation"], row["mode"])
        for row in measurements
    }
    summary_cells = {
        (row["scale"], row["stage"], row["implementation"], row["mode"])
        for row in summary
    }
    correctness_cells = {
        (row["scale"], row["stage"], row["implementation"], row["mode"])
        for row in correctness
    }
    if measurement_cells != summary_cells or measurement_cells != correctness_cells:
        raise ExportError("restricted result and correctness cells are inconsistent")
    measurements_by_cell: dict[
        tuple[str, str, str, str], list[dict[str, str]]
    ] = defaultdict(list)
    for row in measurements:
        measurements_by_cell[
            (
                row["scale"],
                row["stage"],
                row["implementation"],
                row["mode"],
            )
        ].append(row)
    summary_identity_fields = (
        "input_sha256",
        "output_records",
        "semantic_sha256",
        "sort_order",
        "reference_sequences",
        "reference_dictionary_sha256",
    )
    for row in summary:
        key = (
            row["scale"],
            row["stage"],
            row["implementation"],
            row["mode"],
        )
        matching_measurements = measurements_by_cell[key]
        if any(
            row[field]
            != ",".join(
                sorted(
                    {
                        measurement[field]
                        for measurement in matching_measurements
                    }
                )
            )
            for field in summary_identity_fields
        ):
            raise ExportError(
                "restricted summary identity does not match measurements"
            )

    expected_cells: set[tuple[str, str, str, str]] = set()
    expected_comparison_cells: set[tuple[str, str, str, str]] = set()
    expected_treatments: dict[str, tuple[tuple[str, str], ...]] = {}
    for source_id in source_ids:
        treatments: list[tuple[str, str]] = [
            ("canonical-upstream", "legacy"),
            ("dumi", "off"),
        ]
        if workloads[source_id]["streaming_on_eligible"] is True:
            treatments.append(("dumi", "on"))
        treatments.append(("dumi", "auto"))
        expected_treatments[source_id] = tuple(treatments)
        for stage in SAFE_STAGES:
            for implementation, mode in treatments:
                expected_cells.add((source_id, stage, implementation, mode))
        for stage in SAFE_COMPARISON_STAGES:
            for implementation, mode in treatments:
                if implementation != "canonical-upstream":
                    expected_comparison_cells.add(
                        (source_id, stage, implementation, mode)
                    )
    if measurement_cells != expected_cells:
        raise ExportError("restricted benchmark treatment matrix is incomplete")

    comparison_cells = {
        (row["scale"], row["stage"], row["implementation"], row["mode"])
        for row in comparisons
    }
    if (
        len(comparison_cells) != len(comparisons)
        or comparison_cells != expected_comparison_cells
    ):
        raise ExportError("restricted comparison matrix is incomplete")

    by_schedule: dict[
        tuple[str, int, str], dict[tuple[str, str], int]
    ] = defaultdict(dict)
    for row in design:
        schedule = (
            row["scale"],
            positive_integer(row["repetition"], "design repetition"),
            row["stage"],
        )
        treatment = (row["implementation"], row["mode"])
        if treatment in by_schedule[schedule]:
            raise ExportError("restricted schedule repeats a treatment")
        by_schedule[schedule][treatment] = positive_integer(
            row["order"], "design order"
        )
    for workload_index, source_id in enumerate(source_ids):
        treatments = expected_treatments[source_id]
        expected_orders = set(range(1, len(treatments) + 1))
        for repetition in range(1, repetitions + 1):
            for stage in SAFE_STAGES:
                stage_schedule = by_schedule[
                    (source_id, repetition, stage)
                ]
                schedule_row = repetition - 1 + workload_index
                if stage == "end_to_end_ready":
                    schedule_row += 1
                expected_stage_schedule = {
                    treatment: order
                    for order, treatment in enumerate(
                        expected_balanced_order(
                            treatments, schedule_row
                        ),
                        1,
                    )
                }
                if (
                    set(stage_schedule) != set(treatments)
                    or set(stage_schedule.values()) != expected_orders
                    or stage_schedule != expected_stage_schedule
                ):
                    raise ExportError(
                        "restricted schedule order is incomplete or inconsistent"
                    )

    for source_id in source_ids:
        workload_rows = [
            row for row in measurements if row["scale"] == source_id
        ]
        input_hashes = {row["input_sha256"] for row in workload_rows}
        if (
            input_hashes
            != {workloads[source_id]["_validated_input_sha256"]}
        ):
            raise ExportError(
                "restricted measurements contain inconsistent workload inputs"
            )
        expected_reference_sequences = str(
            workloads[source_id]["_validated_reference_sequences"]
        )
        expected_reference_digest = workloads[source_id][
            "_validated_reference_dictionary_sha256"
        ]
        if any(
            row["reference_sequences"] != expected_reference_sequences
            or row["expected_reference_sequences"]
            != expected_reference_sequences
            or row["reference_dictionary_sha256"]
            != expected_reference_digest
            or row["expected_reference_dictionary_sha256"]
            != expected_reference_digest
            for row in workload_rows
        ):
            raise ExportError(
                "restricted measurement headers do not match the input receipt"
            )
        oracle_evidence = workloads[source_id][
            "_validated_oracle_evidence"
        ]
        directional_gate = workloads[source_id][
            "_validated_directional_gate"
        ]
        directional_diagnostics = workloads[source_id][
            "_validated_directional_diagnostics"
        ]
        if any(
            row["oracle_implementation"] not in oracle_evidence
            for row in workload_rows
        ):
            raise ExportError(
                "restricted measurements name an unknown implementation oracle"
            )
        if any(
            any(
                row[field]
                != oracle_evidence[row["oracle_implementation"]][field]
                for field in (
                    "expected_output_records",
                    "expected_semantic_sha256",
                    "expected_reference_sequences",
                    "expected_reference_dictionary_sha256",
                )
            )
            or strict_boolean(
                row["cross_implementation_exact_match"],
                "cross-implementation exact match",
            )
            is not workloads[source_id]["_validated_cross_exact"]
            or strict_boolean(
                row["cross_implementation_output_count_match"],
                "cross-implementation output-count match",
            )
            is not workloads[source_id]["_validated_cross_output_count"]
            or strict_boolean(
                row[
                    "cross_implementation_alignment_group_output_count_match"
                ],
                "cross-implementation alignment-group match",
            )
            is not workloads[source_id]["_validated_cross_alignment_group"]
            or strict_boolean(
                row["directional_oracle_gate_pass"],
                "directional-oracle gate",
            )
            is not directional_gate["directional_oracle_gate_pass"]
            or strict_boolean(
                row["dumi_off_oracle_partition_equivalent"],
                "dUMI-off oracle partition equivalence",
            )
            is not directional_gate[
                "dumi_off_oracle_partition_equivalent"
            ]
            or strict_boolean(
                row["dumi_off_oracle_root_assignment_equivalent"],
                "dUMI-off oracle root equivalence",
            )
            is not directional_gate[
                "dumi_off_oracle_root_assignment_equivalent"
            ]
            or any(
                strict_boolean(row[field], field.replace("_", " "))
                is not directional_diagnostics[field]
                for field in (
                    "canonical_upstream_oracle_partition_equivalent",
                    "canonical_upstream_oracle_root_assignment_equivalent",
                    "canonical_upstream_dumi_off_partition_equivalent",
                    "canonical_upstream_dumi_off_root_assignment_equivalent",
                )
            )
            or row["directional_oracle_receipt"]
            != workloads[source_id]["_validated_directional_receipt"]
            for row in workload_rows
        ):
            raise ExportError(
                "restricted measurements do not match retained oracles"
            )
        rows_by_oracle: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in workload_rows:
            rows_by_oracle[row["oracle_implementation"]].append(row)
        if set(rows_by_oracle) != {"canonical-upstream", "dumi-off"}:
            raise ExportError(
                "restricted measurements are missing implementation oracles"
            )
        exact_fields = ("expected_output_records", "expected_semantic_sha256")
        expected_fields = (
            *exact_fields,
            "expected_reference_sequences",
            "expected_reference_dictionary_sha256",
        )
        oracle_evidence: dict[str, set[tuple[str, ...]]] = {}
        for oracle, oracle_rows in rows_by_oracle.items():
            evidence = {
                tuple(row[field] for field in expected_fields)
                for row in oracle_rows
            }
            if len(evidence) != 1:
                raise ExportError(
                    "restricted measurements contain inconsistent oracle evidence"
                )
            oracle_evidence[oracle] = {
                tuple(row[field] for field in exact_fields)
                for row in oracle_rows
            }
        observed_cross_exact = (
            oracle_evidence["canonical-upstream"]
            == oracle_evidence["dumi-off"]
        )
        observed_output_count_match = {
            evidence[0]
            for evidence in oracle_evidence["canonical-upstream"]
        } == {
            evidence[0] for evidence in oracle_evidence["dumi-off"]
        }
        reported_cross_exact = {
            strict_boolean(
                row["cross_implementation_exact_match"],
                "cross-implementation exact match",
            )
            for row in workload_rows
        }
        reported_output_count_match = {
            strict_boolean(
                row["cross_implementation_output_count_match"],
                "cross-implementation output-count match",
            )
            for row in workload_rows
        }
        if (
            reported_cross_exact != {observed_cross_exact}
            or reported_output_count_match
            != {observed_output_count_match}
        ):
            raise ExportError(
                "restricted cross-implementation evidence is inconsistent"
            )


def write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def write_tsv(
    path: Path, fields: tuple[str, ...], rows: Iterable[Mapping[str, object]]
) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=fields,
            delimiter="\t",
            lineterminator="\n",
            extrasaction="raise",
        )
        writer.writeheader()
        writer.writerows(rows)


def fsync_regular_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_public_stage(stage: Path) -> None:
    for path in sorted(stage.iterdir()):
        if not path.is_file() or path.is_symlink():
            raise ExportError("public staging tree contains a non-regular entry")
        fsync_regular_file(path)
    fsync_directory(stage)


def capture_path_identity(
    path: Path,
    *,
    expected_file_type: int,
    context: str,
) -> PathIdentity:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ExportError(f"could not inspect {context} identity") from error
    if stat.S_IFMT(metadata.st_mode) != expected_file_type:
        raise ExportError(f"{context} has an unexpected file type")
    return PathIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        expected_file_type=expected_file_type,
    )


def path_matches_identity(path: Path, identity: PathIdentity) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return (
        metadata.st_dev == identity.device
        and metadata.st_ino == identity.inode
        and stat.S_IFMT(metadata.st_mode) == identity.expected_file_type
    )


def remove_matching_directory(path: Path, identity: PathIdentity) -> bool:
    if not path_matches_identity(path, identity):
        return False
    shutil.rmtree(path, ignore_errors=True)
    return not path_matches_identity(path, identity)


def unlink_matching_file(path: Path, identity: PathIdentity) -> bool:
    if not path_matches_identity(path, identity):
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return not path_matches_identity(path, identity)


def exporter_git_identity(exporter: Path) -> dict[str, object]:
    repository = exporter.parents[2]
    relative = exporter.relative_to(repository).as_posix()
    try:
        commit = subprocess.run(
            ["git", "-C", os.fspath(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            [
                "git",
                "-C",
                os.fspath(repository),
                "status",
                "--porcelain",
                "--untracked-files=all",
                "--",
                relative,
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        tracked = (
            subprocess.run(
                [
                    "git",
                    "-C",
                    os.fspath(repository),
                    "ls-files",
                    "--error-unmatch",
                    "--",
                    relative,
                ],
                check=False,
                capture_output=True,
            ).returncode
            == 0
        )
        committed_bytes = (
            subprocess.run(
                [
                    "git",
                    "-C",
                    os.fspath(repository),
                    "show",
                    f"{commit}:{relative}",
                ],
                check=True,
                capture_output=True,
            ).stdout
            if tracked
            else None
        )
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        raise ExportError("could not bind the exporter to Git state") from error
    if not GIT_SHA.fullmatch(commit):
        raise ExportError("exporter Git commit is invalid")
    state = (
        "clean"
        if tracked and not status
        else "untracked"
        if not tracked
        else "modified"
    )
    commit_blob_sha256 = (
        hashlib.sha256(committed_bytes).hexdigest()
        if committed_bytes is not None
        else None
    )
    return {
        "repository_url": DUMI_PUBLIC_URL,
        "repository_path": relative,
        "commit_sha": commit,
        "state": state,
        "tracked": tracked,
        "commit_blob_sha256": commit_blob_sha256,
        "matches_commit": commit_blob_sha256 == sha256_file(exporter),
    }


def public_tree_sha256(hashes: Mapping[str, str]) -> str:
    digest = hashlib.sha256()
    for filename, file_digest in sorted(hashes.items()):
        digest.update(filename.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(bytes.fromhex(file_digest))
    return digest.hexdigest()


def validate_exporter_source_binding(
    *,
    publication_grade: bool,
    exporter_git: Mapping[str, object],
    exporter_sha256: str,
    harness_commit_sha: object,
) -> None:
    del publication_grade
    if (
        exporter_git["state"] != "clean"
        or exporter_git["tracked"] is not True
        or exporter_git["matches_commit"] is not True
        or exporter_git["commit_sha"] != harness_commit_sha
        or exporter_git["commit_blob_sha256"] != exporter_sha256
    ):
        raise ExportError(
            "public export requires a clean committed exporter "
            "at the benchmark harness commit"
        )


def public_manifest(
    restricted: Mapping[str, object],
    workloads: Mapping[str, Mapping[str, object]],
    aliases: Mapping[str, str],
    panel_description: str,
    exporter_sha256: str,
    exporter_git: Mapping[str, object],
    evidence_set_id: str,
) -> dict[str, object]:
    config = require_object(restricted["config"], "restricted manifest config")
    canonical = require_object(restricted["canonical"], "canonical source")
    dumi = require_object(restricted["dumi"], "dUMI source")
    dependencies = restricted["dependencies"]
    implementation_sources = restricted["implementation_sources"]
    provenance_ledger = require_object(
        restricted["external_provenance_ledger"],
        "external provenance ledger receipt",
    )
    assert isinstance(dependencies, list)
    assert isinstance(implementation_sources, dict)
    publication_grade = all(
        require_object(
            workload["timing_stage_schedule"], "timing-stage schedule"
        )["publication_grade_external_schedule"]
        is True
        for workload in workloads.values()
    )
    return {
        "format": 2,
        "profile": "public-external-private-fingerprint-free",
        "evidence_set_id": evidence_set_id,
        "exporter": {
            "schema": 1,
            "sha256": exporter_sha256,
            "source_binding": dict(exporter_git),
        },
        "restricted_bundle": {
            "status": "verified-complete",
            "included": False,
            "replay_requires_controlled_access": True,
        },
        "panel": {
            "description": panel_description,
            "workload_count": len(aliases),
        },
        "provenance_attestation": {
            "schema": provenance_ledger["schema"],
            "version": provenance_ledger["version"],
            "authorization_confirmed": provenance_ledger[
                "authorization_confirmed"
            ],
            "pre_deduplication_confirmed": provenance_ledger[
                "pre_deduplication_confirmed"
            ],
            "path_recorded": provenance_ledger["path_recorded"],
            "content_retained": provenance_ledger["content_retained"],
        },
        "sources": {
            "canonical_upstream": {
                "url": canonical["url"],
                "provenance_ref": canonical["provenance_ref"],
                "sha": canonical["sha"],
            },
            "dumi": {
                "url": dumi["url"],
                "sha": dumi["sha"],
                "uncommitted_worktree_sources_excluded": dumi[
                    "uncommitted_worktree_sources_excluded"
                ],
                "worktree_was_dirty": dumi["worktree_was_dirty"],
            },
        },
        "implementation_sources": dict(sorted(implementation_sources.items())),
        "harness": [
            {
                "path": receipt["path"],
                "sha256": receipt["sha256"],
            }
            for receipt in restricted["harness_files"]
        ],
        "harness_commit_binding": restricted["harness_commit_binding"],
        "builds": {
            label: {
                "source_tree_sha256": restricted["builds"][label][
                    "source_tree_sha256"
                ],
                "classes_tree_sha256": restricted["builds"][label][
                    "classes_tree_sha256"
                ],
            }
            for label in ("upstream", "dumi")
        },
        "build_provenance": {
            "source_trees_commit_bound": True,
            "dependency_lock_bound": True,
            "build_commands_exactly_validated": True,
            "compiled_class_trees": (
                "runner-attested-hashes-not-independently-rebuilt-by-exporter"
            ),
        },
        "runtime_id": restricted["runtime_id"],
        "dependencies": [
            {
                "filename": dependency["filename"],
                "sha256": dependency["sha256"],
                "url": dependency["url"],
            }
            for dependency in dependencies
        ],
        "benchmark": {
            "active_processors": config["_validated_active_processors"],
            "repetitions": config["_validated_repetitions"],
            "xms": config["xms"],
            "xmx": config["xmx"],
            "cluster_tag_xmx": config["cluster_tag_xmx"],
            "cluster_sort_command": config["cluster_sort_command"],
            "measured_stages": ["raw", "end_to_end_ready"],
            "stage_orders": "independently-balanced",
            "evidence_class": (
                "publication-grade"
                if publication_grade
                else "exploratory-nonreportable"
            ),
            "publication_grade_external_schedule": publication_grade,
            "jvm_options": restricted["jvm_options"],
            "cluster_tag_jvm_options": restricted["cluster_tag_jvm_options"],
        },
        "workloads": [
            {
                "workload_id": aliases[source_id],
                "paired": workload["paired"],
                "umi_length": workload["umi_length"],
                "umi_separator": workload["umi_separator"],
                "streaming_on_eligible": workload["streaming_on_eligible"],
                "timing_design": {
                    "version": workload["timing_stage_schedule"][
                        "timing_design_version"
                    ],
                    "order_family": workload["timing_stage_schedule"][
                        "order_family"
                    ],
                    "publication_grade_external_schedule": workload[
                        "timing_stage_schedule"
                    ]["publication_grade_external_schedule"],
                },
                "correctness": {
                    "per_implementation_exact_oracle_match": True,
                    "cross_implementation_exact_match": workload[
                        "_validated_cross_exact"
                    ],
                    "cross_implementation_output_count_match": workload[
                        "_validated_cross_output_count"
                    ],
                    "cross_implementation_alignment_group_output_count_match": (
                        workload["_validated_cross_alignment_group"]
                    ),
                    "cross_implementation_bounded_diagnostic_match": workload[
                        "_validated_cross_bounded_diagnostic"
                    ],
                    "directional_oracle": {
                        "method": workload[
                            "_validated_directional_public_method"
                        ],
                        **workload["_validated_directional_gate"],
                        **workload["_validated_directional_diagnostics"],
                        "upstream_agreement_required": False,
                    },
                    "pairwise_cluster_diagnostic": workload[
                        "_validated_pairwise_diagnostic"
                    ],
                    "performance_comparability": {
                        "status": workload[
                            "_validated_performance_comparability"
                        ]["status"],
                        "issues": workload[
                            "_validated_performance_comparability"
                        ]["issues"],
                    },
                },
            }
            for source_id, workload in sorted(
                workloads.items(), key=lambda item: aliases[item[0]]
            )
        ],
        "correctness": {
            "status": "pass",
            "per_implementation_exact_oracle_gate": "pass",
            "required_directional_oracle_gate": "pass",
            "dumi_off_independent_oracle_gate_all": all(
                workload["_validated_directional_gate"][
                    "directional_oracle_gate_pass"
                ]
                is True
                for workload in workloads.values()
            ),
            "upstream_agreement_required": False,
            "upstream_directional_diagnostics_all_match": all(
                all(
                    value is True
                    for value in workload[
                        "_validated_directional_diagnostics"
                    ].values()
                )
                for workload in workloads.values()
            ),
            "cross_implementation_exact_match_all": all(
                workload["_validated_cross_exact"] is True
                for workload in workloads.values()
            ),
            "cross_implementation_output_count_match_all": all(
                workload["_validated_cross_output_count"] is True
                for workload in workloads.values()
            ),
            "cross_implementation_alignment_group_output_count_match_all": all(
                workload["_validated_cross_alignment_group"] is True
                for workload in workloads.values()
            ),
            "cross_implementation_bounded_diagnostic_match_all": all(
                workload["_validated_cross_bounded_diagnostic"] is True
                for workload in workloads.values()
            ),
            "cross_implementation_bounded_diagnostic_scope": {
                "components": [
                    "record-counts",
                    "excluded-unmapped-counts",
                    "excluded-second-of-pair-counts",
                    "ordered-SQ-RG-dictionaries",
                    "alignment-group-output-count-multiset",
                    "pairwise-membership-partition",
                ],
                "full_scientific_or_root_equivalence": False,
            },
            "performance_comparable_all": all(
                workload["_validated_performance_comparability"]["status"]
                == "comparable"
                for workload in workloads.values()
            ),
        },
        "privacy": {
            "private_denylist_review": "applied",
            "private_data_derived_hashes_included": False,
            "private_paths_included": False,
            "raw_or_derived_sequence_included": False,
            "source_record_or_byte_counts_included": False,
        },
    }


def public_environment(environment: Mapping[str, object]) -> dict[str, object]:
    require_allowed_fields(
        environment,
        ENVIRONMENT_ALLOWED_FIELDS,
        ENVIRONMENT_REQUIRED_FIELDS,
        "restricted environment",
    )
    if environment["environment_policy"] != "allowlist":
        raise ExportError("restricted environment policy is invalid")
    logical_cpu_count = positive_integer(
        environment["logical_cpu_count"], "logical CPU count"
    )
    tools = {
        name: first_version_line(environment[name], f"{name} version")
        for name in (
            "java",
            "javac",
            "samtools",
            "gnu_sort",
            "gnu_time",
            "git",
            "python",
        )
    }
    payload: dict[str, object] = {
        "format": 1,
        "platform": safe_one_line(
            environment["platform"], "platform", maximum=500
        ),
        "logical_cpu_count": logical_cpu_count,
        "execution_policy": {
            "environment_allowlisted": True,
            "injection_variables_stripped": True,
            "LANG": "C",
            "LC_ALL": "C",
            "TZ": "UTC",
        },
        "tools": tools,
    }
    governor = environment.get("cpu_scaling_governor")
    if governor is not None:
        payload["cpu_scaling_governor"] = safe_one_line(
            governor, "CPU scaling governor", maximum=100
        )
    return payload


def readme_text(
    panel_description: str,
    workload_count: int,
    publication_grade: bool,
) -> str:
    evidence_class = (
        "publication-grade"
        if publication_grade
        else "exploratory and nonreportable"
    )
    return f"""# Curated external benchmark evidence

This directory is a public, privacy-reviewed projection of a restricted
external-input benchmark. The restricted bundle was COMPLETE and its full
manifest verified before export; an explicit private denylist was applied.
The restricted bundle and private review inputs are not included here.

Panel description: {panel_description}

The exporter binds source trees to the named Git commits, dependencies to the
committed lock, and build commands to the runner contract. Compiled class-tree
hashes remain runner-attested; the exporter does not independently rebuild the
Java bytecode.

The panel contains {workload_count} workloads under neutral aliases. Timing,
CPU, maximum-RSS, schedule, execution-route, matched-comparison, and
correctness fields are retained. Source and result data, private paths,
commands, logs, record and byte counts, and all private-data-derived
fingerprints are omitted. Replaying the source-data tier requires separately
authorized controlled access.

Timing-design classification: {evidence_class}. Consult
`manifest.public.json` for the per-workload order-family and schedule gate.
The per-implementation exact-oracle gate means each timed implementation
matched its own sealed oracle. The required algorithmic gate compares dUMI-off
to a directional-collapse oracle that is independent for directional
clustering, distance evaluation, threshold evaluation, and root construction,
while reusing the audited SAM, QNAME/UMI, alignment-group, header, and
external-sort transport. Upstream agreement is reported as a diagnostic and
is not required for that gate. The bounded diagnostic covers record and
alignment-group counts, excluded-unmapped counts, excluded-second-of-pair
counts, ordered SQ/RG dictionaries, the alignment-group output-count multiset,
and the pairwise membership partition; it is not a full scientific or
root-assignment equivalence claim. Performance ratios are omitted when
implementation output counts differ; those cells remain available as
noncomparable raw measurements.

Files:

- `manifest.public.json`: public code, dependency, configuration, workload,
  correctness, and privacy metadata;
- `environment.public.json`: normalized host class and tool versions;
- `design.public.tsv`: randomized schedule under neutral workload aliases;
- `measurements.public.tsv`: individual timing and resource measurements;
- `summary.public.tsv`: per-cell aggregate measurements and sample counts;
- `comparisons.public.tsv`: matched candidate-versus-upstream ratios;
- `correctness.public.tsv`: private-fingerprint-free correctness attestations;
- `SHA256SUMS`: checksums covering only this public projection.

Run `sha256sum -c SHA256SUMS` in this directory to verify the public files.
"""


def is_default_ignorable(character: str) -> bool:
    codepoint = ord(character)
    return (
        unicodedata.category(character) == "Cf"
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
        or 0xE0000 <= codepoint <= 0xE0FFF
        or codepoint
        in {
            0x034F,
            0x061C,
            0x115F,
            0x1160,
            0x17B4,
            0x17B5,
            0x180B,
            0x180C,
            0x180D,
            0x180F,
            0x3164,
            0xFFA0,
        }
    )


def semantic_strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from semantic_strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from semantic_strings(item)


def validate_public_text_policy(
    text: str,
    denylist: PrivateDenylist,
    *,
    allowed_hashes: set[str],
) -> None:
    normalized_text = unicodedata.normalize("NFKC", text)
    if any(is_default_ignorable(character) for character in normalized_text):
        raise ExportError(
            "public export contains a Unicode default-ignorable character"
        )
    if any(pattern.search(normalized_text) for pattern in PRIVATE_PATH_PATTERNS):
        raise ExportError("public export contains a private absolute path")
    if any(
        match.group(0).rstrip(".,);") not in PUBLIC_URIS
        for match in URI_IN_TEXT.finditer(normalized_text)
    ):
        raise ExportError("public export contains an unapproved URI")
    casefolded = normalized_text.casefold()
    if any(
        unicodedata.normalize("NFKC", token).casefold() in casefolded
        for token in denylist.tokens
    ):
        raise ExportError("public export contains a private source token")
    if any(
        unicodedata.normalize("NFKC", value).casefold() in casefolded
        for value in denylist.paths
    ):
        raise ExportError("public export contains a private source path")
    if any(value.casefold() in casefolded for value in denylist.hashes):
        raise ExportError("public export contains a private source hash")
    for match in HEX_RUN_IN_TEXT.finditer(normalized_text):
        value = match.group(0).lower()
        if len(value) != 64 or value not in allowed_hashes:
            raise ExportError(
                "public export contains an unapproved SHA-256 fingerprint"
            )


def validate_public_tree(
    root: Path,
    denylist: PrivateDenylist | None = None,
    *,
    allowed_hashes: set[str] | None = None,
) -> None:
    supplied_denylist = denylist or PrivateDenylist()
    denylist = PrivateDenylist(
        tokens=BUILTIN_FORBIDDEN_TOKENS + supplied_denylist.tokens,
        paths=supplied_denylist.paths,
        hashes=supplied_denylist.hashes,
    )
    allowed_hashes = {value.lower() for value in (allowed_hashes or set())}
    actual: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ExportError("public export contains a symbolic link")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            actual.add(relative)
            if relative not in PUBLIC_FILES:
                raise ExportError("public export contains an unexpected file")
            payload = path.read_bytes()
            if b"\x00" in payload:
                raise ExportError("public export contains binary or NUL data")
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ExportError("public export contains non-UTF-8 data") from error
            validate_public_text_policy(
                text, denylist, allowed_hashes=allowed_hashes
            )
            if path.suffix == ".json":
                try:
                    value = json.loads(text)
                except json.JSONDecodeError as error:
                    raise ExportError("public export contains invalid JSON") from error
                for scalar in semantic_strings(value):
                    validate_public_text_policy(
                        scalar, denylist, allowed_hashes=allowed_hashes
                    )
    if not actual.issubset(PUBLIC_FILES):
        raise ExportError("public export contains an unexpected file")


def rename_noreplace(source: Path, destination: Path) -> None:
    """Atomically publish a same-filesystem path without replacing a peer."""

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if sys.platform == "darwin":
            rename_exclusive = libc.renamex_np
            rename_exclusive.argtypes = (
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            rename_exclusive.restype = ctypes.c_int
            result = rename_exclusive(
                os.fsencode(source),
                os.fsencode(destination),
                0x00000004,  # RENAME_EXCL
            )
        else:
            rename_exclusive = libc.renameat2
            rename_exclusive.argtypes = (
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            rename_exclusive.restype = ctypes.c_int
            result = rename_exclusive(
                -100,
                os.fsencode(source),
                -100,
                os.fsencode(destination),
                1,  # RENAME_NOREPLACE
            )
    except (OSError, AttributeError) as error:
        raise ExportError(
            "atomic no-overwrite directory publication is unavailable"
        ) from error
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise ExportError("public output path appeared during publication")
    if error_number in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP}:
        raise ExportError(
            "atomic no-overwrite directory publication is unavailable"
        )
    raise ExportError(
        f"could not atomically publish public output: {os.strerror(error_number)}"
    )


def export_public(
    *,
    bundle: Path,
    output: Path,
    alias_map: Path,
    panel_description: str,
    denylist_path: Path,
    private_export_receipt: Path,
    evidence_set_id: str,
) -> None:
    bundle_input = bundle.expanduser().absolute()
    if bundle_input.is_symlink():
        raise ExportError("restricted bundle must not be a symbolic link")
    bundle = bundle_input.resolve()
    output_input = output.expanduser().absolute()
    if os.path.lexists(os.fspath(output_input)):
        raise ExportError("public output path already exists")
    output = output_input.parent.resolve() / output_input.name
    alias_map = alias_map.expanduser().absolute()
    denylist_path = denylist_path.expanduser().absolute()
    receipt_input = private_export_receipt.expanduser().absolute()
    if os.path.lexists(os.fspath(receipt_input)):
        raise ExportError("private export receipt already exists")
    private_export_receipt = receipt_input.parent.resolve() / receipt_input.name
    if os.path.lexists(os.fspath(private_export_receipt)):
        raise ExportError("private export receipt already exists")
    if os.path.lexists(os.fspath(output)):
        raise ExportError("public output path already exists")
    if is_within(output, bundle):
        raise ExportError("public output must be outside the restricted bundle")
    if (
        is_within(private_export_receipt, bundle)
        or is_within(private_export_receipt, output)
        or is_within(output, private_export_receipt)
        or private_export_receipt == output
    ):
        raise ExportError(
            "private export receipt must be outside source and public trees"
        )
    panel_description = safe_one_line(
        panel_description, "panel description", maximum=500
    )
    if not PANEL_DESCRIPTION.fullmatch(panel_description):
        raise ExportError("panel description contains unsafe characters")
    if "://" in panel_description:
        raise ExportError("panel description contains an unsafe URI")
    evidence_set_id = safe_one_line(
        evidence_set_id, "evidence-set ID", maximum=64
    )
    if not EVIDENCE_SET_ID.fullmatch(evidence_set_id):
        raise ExportError("evidence-set ID is not a neutral public identifier")

    verify_restricted_bundle(bundle)
    initial_seal_sha256 = sha256_file(bundle / "MANIFEST.sha256")
    initial_alias_map_sha256 = private_input_sha256(
        alias_map, "private alias map"
    )
    initial_denylist_sha256 = private_input_sha256(
        denylist_path, "private denylist"
    )
    aliases = load_aliases(alias_map)
    denylist = load_denylist(denylist_path)
    restricted = require_object(
        strict_json(bundle / "manifest.json", "restricted manifest"),
        "restricted manifest",
    )
    environment = require_object(
        strict_json(bundle / "environment.json", "restricted environment"),
        "restricted environment",
    )
    source_ids, workloads, dependency_hashes = validate_source_manifest(
        restricted, bundle, environment
    )
    validate_aliases(aliases, source_ids, workloads)
    implicit_source_tokens = tuple(
        source_id
        for source_id in source_ids
        if source_id.casefold() != aliases[source_id].casefold()
    )
    denylist = PrivateDenylist(
        tokens=(
            denylist.tokens
            + BUILTIN_FORBIDDEN_TOKENS
            + implicit_source_tokens
        ),
        paths=denylist.paths,
        hashes=denylist.hashes,
    )
    try:
        validate_public_text_policy(
            output.name, denylist, allowed_hashes=set()
        )
    except ExportError as error:
        raise ExportError(
            "public output directory name violates the privacy policy"
        ) from error
    design_input = read_tsv(
        bundle / "design.tsv", DESIGN_INPUT_FIELDS, "restricted design"
    )
    measurement_input = read_tsv(
        bundle / "measurements.tsv",
        MEASUREMENT_INPUT_FIELDS,
        "restricted measurements",
    )
    summary_input = read_tsv(
        bundle / "summary.tsv", SUMMARY_INPUT_FIELDS, "restricted summary"
    )
    comparison_input = read_tsv(
        bundle / "comparisons.tsv",
        COMPARISON_INPUT_FIELDS,
        "restricted comparisons",
    )
    correctness_input = read_tsv(
        bundle / "correctness.tsv",
        CORRECTNESS_INPUT_FIELDS,
        "restricted correctness",
    )
    require_consistent_tables(
        source_ids,
        workloads,
        design_input,
        measurement_input,
        summary_input,
        comparison_input,
        correctness_input,
        positive_integer(
            require_object(
                restricted["config"], "restricted manifest config"
            )["_validated_repetitions"],
            "repetitions",
        ),
    )

    config = require_object(restricted["config"], "restricted manifest config")
    repetitions = positive_integer(
        config["_validated_repetitions"], "repetitions"
    )
    design = transform_design(design_input, aliases)
    measurements = transform_measurements(
        measurement_input,
        aliases,
        workloads,
        bundle=bundle,
        redacted_paths=restricted_redacted_paths(bundle),
    )
    source_summary = transform_summary(
        summary_input, aliases, workloads, repetitions
    )
    summary = recompute_summary(measurement_input, aliases, workloads)
    require_same_projection(
        source_summary,
        summary,
        ("workload_id", "stage", "implementation", "mode"),
        "summary",
    )
    source_comparisons = transform_comparisons(
        comparison_input, aliases, workloads, repetitions
    )
    comparisons = recompute_comparisons(
        measurement_input, aliases, workloads, repetitions
    )
    require_same_projection(
        source_comparisons,
        comparisons,
        ("workload_id", "stage", "implementation", "mode"),
        "comparisons",
    )
    correctness = transform_correctness(
        correctness_input, aliases, workloads
    )
    design.sort(
        key=lambda row: (
            row["workload_id"],
            row["stage"],
            row["repetition"],
            row["order"],
            row["implementation"],
            row["mode"],
        )
    )
    measurements.sort(
        key=lambda row: (
            row["workload_id"],
            row["stage"],
            row["repetition"],
            row["order"],
            row["implementation"],
            row["mode"],
        )
    )
    summary.sort(
        key=lambda row: (
            row["workload_id"],
            row["stage"],
            row["implementation"],
            row["mode"],
        )
    )
    comparisons.sort(
        key=lambda row: (
            row["workload_id"],
            row["stage"],
            row["implementation"],
            row["mode"],
        )
    )
    correctness.sort(
        key=lambda row: (
            row["workload_id"],
            row["stage"],
            row["implementation"],
            row["mode"],
        )
    )

    public_design_keys = {
        tuple(row[field] for field in DESIGN_PUBLIC_FIELDS) for row in design
    }
    public_measurement_keys = {
        tuple(row[field] for field in DESIGN_PUBLIC_FIELDS)
        for row in measurements
    }
    if public_design_keys != public_measurement_keys:
        raise ExportError("public design and measurement projections are inconsistent")
    if len(public_design_keys) != len(design):
        raise ExportError("public run identifiers are not unique")

    exporter_path = Path(__file__).resolve()
    exporter_sha256 = sha256_file(exporter_path)
    exporter_git = exporter_git_identity(exporter_path)
    publication_grade = all(
        require_object(
            workload["timing_stage_schedule"],
            "timing-stage schedule",
        )["publication_grade_external_schedule"]
        is True
        for workload in workloads.values()
    )
    harness_binding = require_object(
        restricted["harness_commit_binding"],
        "restricted harness commit binding",
    )
    validate_exporter_source_binding(
        publication_grade=publication_grade,
        exporter_git=exporter_git,
        exporter_sha256=exporter_sha256,
        harness_commit_sha=harness_binding["commit_sha"],
    )
    allowed_public_source_hashes = dependency_hashes | {exporter_sha256}
    output.parent.mkdir(parents=True, exist_ok=True)
    private_export_receipt.parent.mkdir(
        parents=True, exist_ok=True, mode=0o700
    )
    require_private_permissions(
        private_export_receipt.parent,
        "private export receipt directory",
    )
    stage = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent)
    )
    receipt_descriptor, receipt_temporary_string = tempfile.mkstemp(
        prefix=f".{private_export_receipt.name}.tmp-",
        dir=private_export_receipt.parent,
    )
    os.close(receipt_descriptor)
    receipt_temporary = Path(receipt_temporary_string)
    receipt_temporary.chmod(0o600)
    stage_identity = capture_path_identity(
        stage,
        expected_file_type=stat.S_IFDIR,
        context="public staging directory",
    )
    receipt_temporary_identity = capture_path_identity(
        receipt_temporary,
        expected_file_type=stat.S_IFREG,
        context="private export receipt temporary file",
    )
    previous_signal_handlers: dict[int, object] = {}

    def interrupt_publication(
        signum: int, _frame: object
    ) -> None:
        raise KeyboardInterrupt(
            f"public export interrupted by signal {signum}"
        )

    for candidate in (signal.SIGHUP, signal.SIGTERM):
        try:
            previous_signal_handlers[candidate] = signal.getsignal(candidate)
            signal.signal(candidate, interrupt_publication)
        except (OSError, ValueError):
            previous_signal_handlers.clear()
            break

    def restore_signal_handlers() -> None:
        while previous_signal_handlers:
            candidate, handler = previous_signal_handlers.popitem()
            signal.signal(candidate, handler)

    try:
        (stage / "README.md").write_text(
            readme_text(
                panel_description,
                len(source_ids),
                publication_grade,
            ),
            encoding="utf-8",
        )
        write_json(
            stage / "manifest.public.json",
            public_manifest(
                restricted,
                workloads,
                aliases,
                panel_description,
                exporter_sha256,
                exporter_git,
                evidence_set_id,
            ),
        )
        write_json(
            stage / "environment.public.json",
            public_environment(environment),
        )
        write_tsv(stage / "design.public.tsv", DESIGN_PUBLIC_FIELDS, design)
        write_tsv(
            stage / "measurements.public.tsv",
            MEASUREMENT_PUBLIC_FIELDS,
            measurements,
        )
        write_tsv(stage / "summary.public.tsv", SUMMARY_PUBLIC_FIELDS, summary)
        write_tsv(
            stage / "comparisons.public.tsv",
            COMPARISON_PUBLIC_FIELDS,
            comparisons,
        )
        write_tsv(
            stage / "correctness.public.tsv",
            CORRECTNESS_PUBLIC_FIELDS,
            correctness,
        )
        validate_public_tree(
            stage,
            denylist,
            allowed_hashes=allowed_public_source_hashes,
        )

        public_hashes: set[str] = set(allowed_public_source_hashes)
        checksum_lines: list[str] = []
        for filename in CHECKSUMMED_PUBLIC_FILES:
            digest = sha256_file(stage / filename)
            public_hashes.add(digest)
            checksum_lines.append(f"{digest}  {filename}\n")
        (stage / "SHA256SUMS").write_text(
            "".join(checksum_lines), encoding="utf-8"
        )
        validate_public_tree(stage, denylist, allowed_hashes=public_hashes)
        if {path.name for path in stage.iterdir()} != set(PUBLIC_FILES):
            raise ExportError("public export file inventory is incomplete")
        fsync_public_stage(stage)
        verify_restricted_bundle(bundle)
        if sha256_file(bundle / "MANIFEST.sha256") != initial_seal_sha256:
            raise ExportError("restricted bundle seal changed during export")
        if (
            private_input_sha256(alias_map, "private alias map")
            != initial_alias_map_sha256
            or private_input_sha256(denylist_path, "private denylist")
            != initial_denylist_sha256
        ):
            raise ExportError("private export input changed during export")
        public_file_hashes = {
            filename: sha256_file(stage / filename)
            for filename in PUBLIC_FILES
        }
        write_json(
            receipt_temporary,
            {
                "format": 1,
                "evidence_set_id": evidence_set_id,
                "restricted_source": {
                    "manifest_sha256": initial_seal_sha256,
                    "manifest_entries": len(
                        (bundle / "MANIFEST.sha256")
                        .read_text(encoding="utf-8")
                        .splitlines()
                    ),
                },
                "private_inputs": {
                    "alias_map_sha256": initial_alias_map_sha256,
                    "denylist_sha256": initial_denylist_sha256,
                },
                "exporter": {
                    "sha256": exporter_sha256,
                    "git": exporter_git,
                },
                "public_projection": {
                    "files": public_file_hashes,
                    "sha256sums_sha256": public_file_hashes["SHA256SUMS"],
                    "tree_sha256": public_tree_sha256(public_file_hashes),
                },
            },
        )
        require_private_permissions(
            receipt_temporary, "private export receipt"
        )
        fsync_regular_file(receipt_temporary)
        fsync_directory(receipt_temporary.parent)
        if (
            os.path.lexists(os.fspath(output))
            or os.path.lexists(os.fspath(private_export_receipt))
        ):
            raise ExportError("export destination appeared during publication")
        try:
            os.link(receipt_temporary, private_export_receipt)
        except OSError as error:
            raise ExportError(
                "could not publish private export receipt without overwrite"
            ) from error
        fsync_directory(private_export_receipt.parent)
        rename_noreplace(stage, output)
        fsync_directory(output.parent)
        receipt_temporary.unlink()
        fsync_directory(receipt_temporary.parent)
    except BaseException:
        restore_signal_handlers()
        output_removed = remove_matching_directory(output, stage_identity)
        output_retained = path_matches_identity(output, stage_identity)
        stage_removed = remove_matching_directory(stage, stage_identity)
        if output_removed or stage_removed:
            try:
                fsync_directory(output.parent)
            except OSError:
                pass
        if not output_retained and unlink_matching_file(
            private_export_receipt,
            receipt_temporary_identity,
        ):
            try:
                fsync_directory(private_export_receipt.parent)
            except OSError:
                pass
        if unlink_matching_file(
            receipt_temporary,
            receipt_temporary_identity,
        ):
            try:
                fsync_directory(receipt_temporary.parent)
            except OSError:
                pass
        raise
    finally:
        restore_signal_handlers()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "verify a restricted external benchmark bundle and export a "
            "private-data-fingerprint-free public projection"
        )
    )
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--alias-map", required=True, type=Path)
    parser.add_argument("--panel-description", required=True)
    parser.add_argument("--private-denylist", required=True, type=Path)
    parser.add_argument("--private-export-receipt", required=True, type=Path)
    parser.add_argument("--evidence-set-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    os.umask(0o077)
    args = build_parser().parse_args(argv)
    try:
        export_public(
            bundle=args.bundle,
            output=args.output_dir,
            alias_map=args.alias_map,
            panel_description=args.panel_description,
            denylist_path=args.private_denylist,
            private_export_receipt=args.private_export_receipt,
            evidence_set_id=args.evidence_set_id,
        )
    except ExportError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print("Public external benchmark projection exported successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
