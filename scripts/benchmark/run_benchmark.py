#!/usr/bin/env python3
"""Run the reproducible public upstream-versus-dUMI benchmark matrix.

The timed Java code is always compiled from archived Git commits.  The runner
itself may be launched from a development checkout, but it never benchmarks
uncommitted production sources.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import getpass
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence


CANONICAL_URL = "https://github.com/Daniel-Liu-c0deb0t/UMICollapse.git"
CANONICAL_SHA = "efeab35f5d29dec1d496ade3f681eeb34d9c2057"
CANONICAL_REF = "refs/heads/master"
INTERMEDIATE_URL = "https://github.com/siddharthab/UMICollapse.git"
INTERMEDIATE_SHA = "aeacd8231cf8e77c03d03139ed6e65a4c2845015"
INTERMEDIATE_REF = "refs/heads/master"
DUMI_PUBLIC_URL = "https://github.com/justinblethrow-cloud/dUMI.git"
STREAMING_MARKER = "Using coordinate-sorted single-end streaming fast path"
STREAMING_FALLBACK_MARKER = (
    "Streaming fast path was not safe for this input; retrying with "
    "--streaming-mode off"
)
PAIRED_STREAMING_REJECTION = (
    "error: Streaming mode requires single-end, single-threaded execution "
    "without --tag."
)
STREAMING_DATA_REJECTION_MARKERS = {
    "record-order": (
        "DeduplicateSAM$StreamingFallbackException: Streaming mode requires "
        "records to be in coordinate order, but read "
    ),
    "reverse-coordinate-overflow": (
        "DeduplicateSAM$StreamingFallbackException: Streaming mode cannot "
        "represent the reverse-strand unclipped end for read "
    ),
    "positive-lag-window": (
        "DeduplicateSAM$StreamingFallbackException: Streaming positive-lag "
        "window is too small for read "
    ),
}
BENCHMARK_ENVIRONMENT = {
    "LANG": "C",
    "LC_ALL": "C",
    "TZ": "UTC",
}
INJECTION_ENVIRONMENT_VARIABLES = (
    "CLASSPATH",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
    "JDK_JAVA_OPTIONS",
    "JAVA_TOOL_OPTIONS",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "_JAVA_OPTIONS",
)
ACTIVE_OUTPUT_ROOT: Path | None = None
ACTIVE_EXTERNAL_INPUT_MODE = False
PUBLIC_PATH_REPLACEMENTS: list[tuple[str, str]] = []
MEASURED_STAGES = ("raw", "end_to_end_ready")

MEASUREMENT_COLUMNS = [
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
]

DIRECTIONAL_ORACLE_SCHEMA = "dumi-directional-oracle-check-v1"
DIRECTIONAL_ORACLE_SCHEMA_VERSION = 1
DIRECTIONAL_ORACLE_METHODS = {
    "membership_oracle": "string-hamming-directional-v1",
    "root_total_order": "dumi-bitset-signed-chunks-v1",
    "threshold": "java-binary32-directional-threshold-v1",
    "membership_partition": "alignment-cluster-umi-frequency-v1",
    "rooted_partition": "alignment-root-umi-frequency-v1",
}
DIRECTIONAL_ORACLE_GATE_FIELDS = (
    "directional_oracle_gate_pass",
    "dumi_off_oracle_partition_equivalent",
    "dumi_off_oracle_root_assignment_equivalent",
    "dumi_off_source_reference_dictionary_equivalent",
    "dumi_off_source_read_group_dictionary_equivalent",
)
DIRECTIONAL_ORACLE_DIAGNOSTIC_FIELDS = (
    "canonical_upstream_oracle_partition_equivalent",
    "canonical_upstream_oracle_root_assignment_equivalent",
    "canonical_upstream_dumi_off_partition_equivalent",
    "canonical_upstream_dumi_off_root_assignment_equivalent",
    "canonical_upstream_source_reference_dictionary_equivalent",
    "canonical_upstream_source_read_group_dictionary_equivalent",
)
DIRECTIONAL_ORACLE_METRIC_COUNT_FIELDS = (
    "input_bytes",
    "records",
    "alignment_groups",
    "clusters",
    "umi_memberships",
    "max_umi_memberships_per_cluster",
    "membership_partition_bytes",
    "rooted_partition_bytes",
    "alignment_umi_frequency_multiset_bytes",
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
)
DIRECTIONAL_ORACLE_METRIC_SHA256_FIELDS = (
    "input_sha256",
    "membership_partition_sha256",
    "rooted_partition_sha256",
    "alignment_umi_frequency_multiset_sha256",
    "reference_dictionary_sha256",
    "read_group_dictionary_sha256",
)
NONCOMPARABLE_OUTPUT_COUNT_ISSUE = (
    "cross-implementation-output-count-mismatch"
)


class BenchmarkError(RuntimeError):
    """A benchmark contract or external command failed."""


class BenchmarkSignalInterrupt(KeyboardInterrupt):
    """A termination signal converted into a normal Python stack unwind."""

    def __init__(self, signal_number: int):
        super().__init__(signal_number)
        self.signal_number = signal_number


@dataclass(frozen=True)
class ExternalBamInput:
    workload_id: str
    bam_path: Path
    bam_sha256: str
    paired: bool
    umi_length: int
    umi_separator: str
    rationale: str


@dataclass(frozen=True)
class Workload:
    name: str
    scale: str
    umi_length: int
    paired: bool
    generator_args: tuple[str, ...]
    umi_separator: str = "_"
    external_input: ExternalBamInput | None = None
    streaming_on_eligible: bool | None = None

    @property
    def input_mode(self) -> str:
        return "external_bam" if self.external_input is not None else "synthetic"


@dataclass(frozen=True)
class Implementation:
    name: str
    mode: str
    source_key: str

    @property
    def label(self) -> str:
        return self.name if self.mode == "legacy" else f"{self.name}-{self.mode}"


@dataclass(frozen=True)
class ScheduledCell:
    repetition: int
    order: int
    implementation: Implementation


@dataclass
class PendingTimedCell:
    stage: str
    cell: ScheduledCell
    run_id: str
    stage_root: Path
    java_output: Path
    measured_output: Path
    expected_raw_sort_order: str
    actual_route: str
    exit_code: int
    metrics: tuple[str, str, str, str, str, str]
    exact_reference: Path | None
    exact_reference_canonical: Path | None
    exact_reference_canonical_receipt: Path | None
    exact_expectation: dict[str, object]
    oracle_implementation: str


def implementations_for(
    workload: Workload, include_intermediate: bool
) -> list[Implementation]:
    if workload.paired:
        implementations = [
            Implementation("canonical-upstream", "legacy", "upstream"),
        ]
        if include_intermediate and workload.external_input is None:
            implementations.append(
                Implementation("intermediate-pr32", "legacy", "intermediate")
            )
        implementations.extend(
            [
                Implementation("dumi", "off", "dumi"),
                Implementation("dumi", "auto", "dumi"),
            ]
        )
        return implementations
    implementations = [
        Implementation("canonical-upstream", "legacy", "upstream"),
        Implementation("dumi", "off", "dumi"),
    ]
    if workload.streaming_on_eligible is not False:
        implementations.append(Implementation("dumi", "on", "dumi"))
    implementations.append(Implementation("dumi", "auto", "dumi"))
    return implementations


def _raise_signal_interrupt(
    signal_number: int, _frame: object
) -> None:
    """Ignore repeats, then make ordinary control flow unwind for cleanup."""
    ignore_termination_signals()
    raise BenchmarkSignalInterrupt(signal_number)


def install_termination_signal_handlers() -> None:
    """Convert scheduler termination signals into cleanup-safe exceptions."""
    for signal_name in ("SIGHUP", "SIGTERM"):
        signal_number = getattr(signal, signal_name, None)
        if signal_number is not None:
            signal.signal(signal_number, _raise_signal_interrupt)


def ignore_termination_signals() -> None:
    """Prevent a repeated scheduler signal from interrupting failure cleanup."""
    for signal_name in ("SIGHUP", "SIGTERM"):
        signal_number = getattr(signal, signal_name, None)
        if signal_number is not None:
            signal.signal(signal_number, signal.SIG_IGN)


def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_status(output_root: Path, state: str, detail: str = "") -> None:
    payload = {
        "state": state,
        "updated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "detail": sanitize_public_text(detail),
    }
    temporary = output_root / "STATUS.json.tmp"
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(output_root / "STATUS.json")


def summarize_partial_failure(output_root: Path) -> None:
    measurements = output_root / "measurements.tsv"
    design = output_root / "design.tsv"
    manifest_path = output_root / "manifest.json"
    summarizer = output_root / "harness" / "summarize_results.py"
    if not summarizer.is_file():
        summarizer = Path(__file__).resolve().parent / "summarize_results.py"
    if not (measurements.is_file() and design.is_file() and manifest_path.is_file()):
        return
    try:
        repetitions = str(
            json.loads(manifest_path.read_text(encoding="utf-8"))["config"]["repetitions"]
        )
    except (KeyError, TypeError, json.JSONDecodeError, OSError):
        return
    command = [
        sys.executable,
        os.fspath(summarizer),
        os.fspath(measurements),
        "--output",
        os.fspath(output_root / "partial-summary.tsv"),
        "--correctness-output",
        os.fspath(output_root / "partial-correctness.tsv"),
        "--comparisons-output",
        os.fspath(output_root / "partial-comparisons.tsv"),
        "--design-tsv",
        os.fspath(design),
        "--expected-repetitions",
        repetitions,
    ]
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    (output_root / "partial-summarizer-command.txt").write_text(
        command_text(command) + "\n", encoding="utf-8"
    )
    (output_root / "partial-summarizer-stdout.txt").write_text(
        sanitize_public_text(completed.stdout or ""), encoding="utf-8"
    )
    (output_root / "partial-summarizer-stderr.txt").write_text(
        sanitize_public_text(completed.stderr or ""), encoding="utf-8"
    )


def sanitize_public_text(value: str) -> str:
    sanitized = value
    for private, replacement in sorted(
        PUBLIC_PATH_REPLACEMENTS, key=lambda item: len(item[0]), reverse=True
    ):
        sanitized = sanitized.replace(private, replacement)
    return sanitized


def sanitize_text_file(path: Path | None) -> None:
    if path is None or not path.is_file():
        return
    original = path.read_text(encoding="utf-8", errors="replace")
    sanitized = sanitize_public_text(original)
    if sanitized != original:
        path.write_text(sanitized, encoding="utf-8")


def command_text(command: Sequence[os.PathLike[str] | str]) -> str:
    return shlex.join([sanitize_public_text(os.fspath(item)) for item in command])


def require_tool(name: str, explicit: str | None = None) -> Path:
    candidate = explicit or shutil.which(name)
    if not candidate:
        raise BenchmarkError(f"required tool was not found: {name}")
    path = Path(candidate).resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise BenchmarkError(f"required tool is not executable: {path}")
    return path


PROCESS_GROUP_TERM_GRACE_SECONDS = 0.5
PROCESS_GROUP_KILL_WAIT_SECONDS = 5.0


def process_group_exists(process_group_id: int) -> bool:
    """Return whether a POSIX process group still has any members."""
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_process_group(process: subprocess.Popen[str]) -> None:
    """Terminate and reap a command and every descendant in its process group."""
    if os.name != "posix":
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=PROCESS_GROUP_TERM_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
        process.wait()
        return

    process_group_id = process.pid
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        pass

    deadline = time.monotonic() + PROCESS_GROUP_TERM_GRACE_SECONDS
    while process_group_exists(process_group_id) and time.monotonic() < deadline:
        time.sleep(0.01)

    if process_group_exists(process_group_id):
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass

    try:
        process.wait(timeout=PROCESS_GROUP_KILL_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.kill()
        process.wait()


def run_command(
    command: Sequence[os.PathLike[str] | str],
    *,
    cwd: Path | None = None,
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
    sanitize_logs: bool = True,
) -> subprocess.CompletedProcess[str]:
    command_strings = [os.fspath(item) for item in command]
    stdout_handle = stdout_path.open("w", encoding="utf-8") if stdout_path else None
    stderr_handle = stderr_path.open("w", encoding="utf-8") if stderr_path else None
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command_strings,
            cwd=cwd,
            stdout=stdout_handle if stdout_handle else subprocess.PIPE,
            stderr=stderr_handle if stderr_handle else subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            start_new_session=(os.name == "posix"),
        )
        try:
            stdout, stderr = process.communicate()
        except BaseException:
            terminate_process_group(process)
            raise
        completed = subprocess.CompletedProcess(
            command_strings,
            process.returncode,
            stdout,
            stderr,
        )
    finally:
        if stdout_handle:
            stdout_handle.close()
        if stderr_handle:
            stderr_handle.close()
    if sanitize_logs:
        sanitize_text_file(stdout_path)
        sanitize_text_file(stderr_path)

    if check and completed.returncode != 0:
        stderr = completed.stderr.strip() if completed.stderr else ""
        detail = f": {sanitize_public_text(stderr)}" if stderr else ""
        raise BenchmarkError(
            f"command exited {completed.returncode}: {command_text(command_strings)}{detail}"
        )
    return completed


def capture(command: Sequence[os.PathLike[str] | str], cwd: Path | None = None) -> str:
    completed = run_command(command, cwd=cwd)
    return ((completed.stdout or "") + (completed.stderr or "")).strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


EXTERNAL_WORKLOAD_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
SHA256_HEX = re.compile(r"^[0-9a-fA-F]{64}$")
SAFE_UMI_SEPARATOR = re.compile(r"^[._:+-]{1,8}$")
EXTERNAL_MANIFEST_FIELDS = {
    "workload_id",
    "bam_path",
    "bam_sha256",
    "paired",
    "umi_length",
    "umi_separator",
    "rationale",
}
EXTERNAL_MANIFEST_REQUIRED_FIELDS = EXTERNAL_MANIFEST_FIELDS - {"rationale"}
EXTERNAL_PROVENANCE_LEDGER_SCHEMA = "dumi-external-provenance-ledger"
EXTERNAL_PROVENANCE_LEDGER_VERSION = 1
EXTERNAL_PROVENANCE_LEDGER_WORKLOAD_FIELDS = {
    "workload_id",
    "authorization_confirmed",
    "pre_deduplication_confirmed",
    "bam_sha256",
}
LOWERCASE_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def parse_manifest_boolean(value: object, context: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise BenchmarkError(f"{context}: paired must be true or false")


def external_manifest_rows(path: Path) -> list[dict[str, object]]:
    try:
        if path.suffix.lower() == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                unknown_top_level = set(payload) - {"format", "workloads"}
                if unknown_top_level:
                    raise BenchmarkError(
                        "external JSON manifest has unknown top-level fields: "
                        + ", ".join(sorted(unknown_top_level))
                    )
                if payload.get("format", 1) != 1:
                    raise BenchmarkError("external JSON manifest format must be 1")
                payload = payload.get("workloads")
            if not isinstance(payload, list):
                raise BenchmarkError(
                    "external JSON manifest must be an array or contain a workloads array"
                )
            rows: list[dict[str, object]] = []
            for index, row in enumerate(payload, 1):
                if not isinstance(row, dict):
                    raise BenchmarkError(
                        f"external JSON manifest row {index} must be an object"
                    )
                rows.append(dict(row))
            return rows

        if path.suffix.lower() not in {".tsv", ".txt"}:
            raise BenchmarkError(
                "external manifest filename must end in .json, .tsv, or .txt"
            )
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if not reader.fieldnames:
                raise BenchmarkError("external TSV manifest has no header")
            if None in reader.fieldnames or len(reader.fieldnames) != len(
                set(reader.fieldnames)
            ):
                raise BenchmarkError(
                    "external TSV manifest has an empty or duplicate header"
                )
            rows = []
            for line_number, row in enumerate(reader, 2):
                if None in row:
                    raise BenchmarkError(
                        f"external TSV manifest line {line_number} has too many fields"
                    )
                rows.append(
                    {
                        key: "" if value is None else value
                        for key, value in row.items()
                    }
                )
            return rows
    except UnicodeDecodeError as error:
        raise BenchmarkError("external manifest is not valid UTF-8 text") from error
    except json.JSONDecodeError as error:
        raise BenchmarkError(
            f"external JSON manifest is malformed at line {error.lineno}, "
            f"column {error.colno}"
        ) from error
    except OSError as error:
        raise BenchmarkError(
            f"could not read external manifest: {sanitize_public_text(str(error))}"
        ) from error


def parse_external_manifest(path_string: str) -> list[ExternalBamInput]:
    global PUBLIC_PATH_REPLACEMENTS
    manifest_path = Path(path_string).expanduser().resolve()
    PUBLIC_PATH_REPLACEMENTS.append(
        (os.fspath(manifest_path), "<EXTERNAL_MANIFEST>")
    )
    if not manifest_path.is_file():
        raise BenchmarkError("external manifest is not a regular file")

    raw_rows = external_manifest_rows(manifest_path)
    if not raw_rows:
        raise BenchmarkError("external manifest contains no workloads")

    entries: list[ExternalBamInput] = []
    seen_ids: set[str] = set()
    seen_paths: set[Path] = set()
    seen_hashes: set[str] = set()
    for row_index, row in enumerate(raw_rows, 1):
        fields = set(row)
        missing = sorted(EXTERNAL_MANIFEST_REQUIRED_FIELDS - fields)
        unknown = sorted(fields - EXTERNAL_MANIFEST_FIELDS)
        if missing:
            raise BenchmarkError(
                f"external manifest row {row_index} is missing fields: "
                + ", ".join(missing)
            )
        if unknown:
            raise BenchmarkError(
                f"external manifest row {row_index} has unknown fields: "
                + ", ".join(unknown)
            )
        context = f"external manifest row {row_index}"
        workload_id = str(row["workload_id"]).strip()
        if (
            len(workload_id) > 64
            or not EXTERNAL_WORKLOAD_ID.fullmatch(workload_id)
        ):
            raise BenchmarkError(
                f"{context}: workload_id must be a neutral lowercase slug "
                "of at most 64 characters"
            )
        if workload_id in seen_ids:
            raise BenchmarkError(f"{context}: duplicate workload_id {workload_id!r}")
        seen_ids.add(workload_id)

        raw_bam_path = str(row["bam_path"]).strip()
        if not raw_bam_path:
            raise BenchmarkError(f"{context}: bam_path must not be empty")
        bam_path = Path(raw_bam_path).expanduser()
        if not bam_path.is_absolute():
            bam_path = manifest_path.parent / bam_path
        bam_path = bam_path.resolve()
        PUBLIC_PATH_REPLACEMENTS.append(
            (os.fspath(bam_path), f"<EXTERNAL_BAM:{workload_id}>")
        )
        if bam_path in seen_paths:
            raise BenchmarkError(f"{context}: BAM path is used by more than one row")
        seen_paths.add(bam_path)
        if bam_path.suffix.lower() != ".bam":
            raise BenchmarkError(f"{context}: bam_path must name a .bam file")
        if not bam_path.is_file():
            raise BenchmarkError(f"{context}: bam_path is not a regular file")

        bam_sha256 = str(row["bam_sha256"]).strip().lower()
        if not SHA256_HEX.fullmatch(bam_sha256):
            raise BenchmarkError(
                f"{context}: bam_sha256 must contain exactly 64 hexadecimal characters"
            )
        if bam_sha256 in seen_hashes:
            raise BenchmarkError(
                f"{context}: BAM content hash is used by more than one row"
            )
        seen_hashes.add(bam_sha256)
        paired = parse_manifest_boolean(row["paired"], context)
        raw_umi_length = row["umi_length"]
        if isinstance(raw_umi_length, bool):
            raise BenchmarkError(f"{context}: umi_length must be a positive integer")
        try:
            umi_length = int(str(raw_umi_length).strip())
        except ValueError as error:
            raise BenchmarkError(
                f"{context}: umi_length must be a positive integer"
            ) from error
        if umi_length <= 1:
            raise BenchmarkError(
                f"{context}: umi_length must be greater than the fixed edit distance k=1"
            )

        raw_umi_separator = row["umi_separator"]
        if not isinstance(raw_umi_separator, str):
            raise BenchmarkError(f"{context}: umi_separator must be a string")
        umi_separator = raw_umi_separator
        try:
            umi_separator.encode("ascii")
        except UnicodeEncodeError as error:
            raise BenchmarkError(
                f"{context}: umi_separator must contain only ASCII characters"
            ) from error
        if (
            SAFE_UMI_SEPARATOR.fullmatch(umi_separator) is None
            or umi_separator.startswith("-")
        ):
            raise BenchmarkError(
                f"{context}: umi_separator must contain 1-8 characters drawn "
                "only from ._:+- and must not start with '-'"
            )
        raw_rationale = row.get("rationale", "")
        rationale = (
            ""
            if raw_rationale is None
            else str(raw_rationale).strip()
        )
        if (
            len(rationale) > 500
            or any(character in "\r\n\t\x00" for character in rationale)
        ):
            raise BenchmarkError(
                f"{context}: rationale must be at most 500 characters on one line"
            )
        if (
            any(
                root in rationale
                for root in (
                    "/" + "mnt" + "/",
                    "/" + "home" + "/",
                    "/" + "Users" + "/",
                )
            )
            or sanitize_public_text(rationale) != rationale
        ):
            raise BenchmarkError(
                f"{context}: rationale must not contain a private path or local identity"
            )

        entries.append(
            ExternalBamInput(
                workload_id=workload_id,
                bam_path=bam_path,
                bam_sha256=bam_sha256,
                paired=paired,
                umi_length=umi_length,
                umi_separator=umi_separator,
                rationale=rationale,
            )
        )
    return entries


def verify_external_provenance_ledger_hash(
    path: Path, expected_sha256: str
) -> None:
    if not path.is_file() or path.is_symlink():
        raise BenchmarkError(
            "external provenance ledger is not a regular non-symlink file"
        )
    if sha256_file(path) != expected_sha256:
        raise BenchmarkError(
            "external provenance ledger SHA-256 does not match the required "
            "digest"
        )


def validate_external_provenance_ledger(
    *,
    path_string: str,
    expected_sha256: str,
    external_entries: Sequence[ExternalBamInput],
) -> tuple[Path, dict[str, object]]:
    """Validate and bind a private authorization/provenance ledger."""
    global PUBLIC_PATH_REPLACEMENTS
    expected_sha256 = expected_sha256.strip().lower()
    if SHA256_HEX.fullmatch(expected_sha256) is None:
        raise BenchmarkError(
            "--external-provenance-ledger-sha256 must contain exactly "
            "64 hexadecimal characters"
        )
    unresolved = Path(path_string).expanduser()
    if unresolved.is_symlink():
        raise BenchmarkError(
            "external provenance ledger must not be a symbolic link"
        )
    path = unresolved.resolve()
    PUBLIC_PATH_REPLACEMENTS.append(
        (os.fspath(path), "<EXTERNAL_PROVENANCE_LEDGER>")
    )
    verify_external_provenance_ledger_hash(path, expected_sha256)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BenchmarkError(
            "external provenance ledger is not valid UTF-8 JSON"
        ) from error
    if not isinstance(payload, dict):
        raise BenchmarkError("external provenance ledger must be a JSON object")
    if (
        payload.get("schema") != EXTERNAL_PROVENANCE_LEDGER_SCHEMA
        or payload.get("version") != EXTERNAL_PROVENANCE_LEDGER_VERSION
        or isinstance(payload.get("version"), bool)
    ):
        raise BenchmarkError(
            "external provenance ledger schema/version is not supported"
        )
    workloads = payload.get("workloads")
    if not isinstance(workloads, list):
        raise BenchmarkError(
            "external provenance ledger workloads must be an array"
        )
    observed_ids: list[str] = []
    observed_bam_sha256: dict[str, str] = {}
    for index, workload in enumerate(workloads, 1):
        if not isinstance(workload, dict):
            raise BenchmarkError(
                f"external provenance ledger workload {index} must be an object"
            )
        fields = set(workload)
        missing = sorted(EXTERNAL_PROVENANCE_LEDGER_WORKLOAD_FIELDS - fields)
        unknown = sorted(fields - EXTERNAL_PROVENANCE_LEDGER_WORKLOAD_FIELDS)
        if missing:
            raise BenchmarkError(
                f"external provenance ledger workload {index} is missing fields: "
                + ", ".join(missing)
            )
        if unknown:
            raise BenchmarkError(
                f"external provenance ledger workload {index} has unknown fields: "
                + ", ".join(unknown)
            )
        workload_id = workload.get("workload_id")
        if not isinstance(workload_id, str) or not workload_id:
            raise BenchmarkError(
                f"external provenance ledger workload {index} has no "
                "workload_id"
            )
        observed_ids.append(workload_id)
        bam_sha256 = workload.get("bam_sha256")
        if (
            not isinstance(bam_sha256, str)
            or LOWERCASE_SHA256_HEX.fullmatch(bam_sha256) is None
        ):
            raise BenchmarkError(
                f"external provenance ledger workload {workload_id!r} "
                "bam_sha256 must contain exactly 64 lowercase hexadecimal "
                "characters"
            )
        observed_bam_sha256[workload_id] = bam_sha256
        if workload.get("authorization_confirmed") is not True:
            raise BenchmarkError(
                f"external provenance ledger workload {workload_id!r} does "
                "not confirm authorization"
            )
        if workload.get("pre_deduplication_confirmed") is not True:
            raise BenchmarkError(
                f"external provenance ledger workload {workload_id!r} does "
                "not confirm pre-deduplication input status"
            )
    if len(observed_ids) != len(set(observed_ids)):
        raise BenchmarkError(
            "external provenance ledger contains duplicate workload IDs"
        )
    expected_ids = {entry.workload_id for entry in external_entries}
    if set(observed_ids) != expected_ids:
        raise BenchmarkError(
            "external provenance ledger workload IDs do not exactly match "
            "the external BAM manifest"
        )
    expected_by_id = {
        entry.workload_id: entry.bam_sha256 for entry in external_entries
    }
    for workload_id in observed_ids:
        if observed_bam_sha256[workload_id] != expected_by_id[workload_id]:
            raise BenchmarkError(
                f"external provenance ledger workload {workload_id!r} "
                "bam_sha256 does not exactly match the external BAM manifest"
            )
    return path, {
        "schema": EXTERNAL_PROVENANCE_LEDGER_SCHEMA,
        "version": EXTERNAL_PROVENANCE_LEDGER_VERSION,
        "sha256": expected_sha256,
        "workload_count": len(observed_ids),
        "authorization_confirmed": True,
        "pre_deduplication_confirmed": True,
        "path_recorded": False,
        "content_retained": False,
    }


def external_header_summary(
    *, samtools: Path, bam_path: Path
) -> tuple[str, int, str]:
    completed = run_command([samtools, "view", "-H", bam_path])
    sort_order = "unknown"
    reference_sequences = 0
    reference_digest = hashlib.sha256()
    for line in (completed.stdout or "").splitlines():
        if line.startswith("@HD\t"):
            for field in line.split("\t")[1:]:
                if field.startswith("SO:"):
                    sort_order = field[3:] or "unknown"
                    break
        elif line.startswith("@SQ\t"):
            reference_digest.update(line.encode("utf-8"))
            reference_digest.update(b"\n")
            reference_sequences += 1
    return sort_order, reference_sequences, reference_digest.hexdigest()


def java_pattern_quote(value: str) -> str:
    """Return the literal-pattern spelling used by canonical upstream."""
    return "\\Q" + value.replace("\\E", "\\E\\\\E\\Q") + "\\E"


def qname_has_requested_umi(
    qname: str, umi_separator: str, umi_length: int
) -> bool:
    match = re.fullmatch(
        r".*"
        + re.escape(umi_separator)
        + r"([ATCGN]+)(.*?)",
        qname,
        flags=re.IGNORECASE,
    )
    return match is not None and len(match.group(1)) >= umi_length


def validate_external_records(
    *,
    entry: ExternalBamInput,
    samtools: Path,
    validation_root: Path,
) -> dict[str, int]:
    command = [samtools, "view", entry.bam_path]
    (validation_root / "records-command.txt").write_text(
        command_text(command) + "\n", encoding="utf-8"
    )
    stderr_path = validation_root / "records-stderr.txt"
    total_records = 0
    mapped_records = 0
    paired_records = 0
    first_records = 0
    second_records = 0
    qnames_checked = 0
    process: subprocess.Popen[bytes] | None = None
    try:
        with stderr_path.open("wb") as stderr_handle:
            try:
                process = subprocess.Popen(
                    [os.fspath(item) for item in command],
                    stdout=subprocess.PIPE,
                    stderr=stderr_handle,
                    env=os.environ.copy(),
                )
            except OSError as error:
                raise BenchmarkError(
                    f"could not validate records for workload "
                    f"{entry.workload_id!r}"
                ) from error
            assert process.stdout is not None
            try:
                for raw_line in process.stdout:
                    fields = raw_line.rstrip(b"\r\n").split(b"\t", 2)
                    if len(fields) < 3:
                        raise BenchmarkError(
                            f"samtools emitted a malformed record while validating "
                            f"workload {entry.workload_id!r}"
                        )
                    try:
                        qname = fields[0].decode("utf-8")
                        flag = int(fields[1])
                    except (UnicodeDecodeError, ValueError) as error:
                        raise BenchmarkError(
                            f"samtools emitted an invalid QNAME or flag while "
                            f"validating workload {entry.workload_id!r}"
                        ) from error
                    total_records += 1
                    is_paired = bool(flag & 0x1)
                    is_unmapped = bool(flag & 0x4)
                    is_first = bool(flag & 0x40)
                    is_second = bool(flag & 0x80)
                    if is_paired:
                        paired_records += 1
                    if is_first:
                        first_records += 1
                    if is_second:
                        second_records += 1
                    if is_unmapped:
                        continue
                    mapped_records += 1
                    if entry.paired and is_second:
                        continue
                    qnames_checked += 1
                    if not qname_has_requested_umi(
                        qname, entry.umi_separator, entry.umi_length
                    ):
                        raise BenchmarkError(
                            f"mapped record {total_records} in external workload "
                            f"{entry.workload_id!r} has no parseable UMI of the "
                            "requested length; the QNAME was not retained"
                        )
            finally:
                process.stdout.close()
            returncode = process.wait()
    finally:
        if process is not None and process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            process.wait()
    sanitize_text_file(stderr_path)
    if returncode != 0:
        raise BenchmarkError(
            f"samtools record scan failed for external workload "
            f"{entry.workload_id!r}"
        )
    if total_records == 0 or mapped_records == 0 or qnames_checked == 0:
        raise BenchmarkError(
            f"external workload {entry.workload_id!r} has no mapped records "
            "eligible for UMI validation"
        )
    if entry.paired:
        if (
            paired_records != total_records
            or first_records == 0
            or second_records == 0
        ):
            raise BenchmarkError(
                f"external workload {entry.workload_id!r} was declared paired "
                "but its record flags are not consistently paired"
            )
    elif paired_records != 0:
        raise BenchmarkError(
            f"external workload {entry.workload_id!r} was declared single-end "
            "but contains paired records"
        )
    return {
        "total_records": total_records,
        "mapped_records": mapped_records,
        "paired_records": paired_records,
        "qnames_checked": qnames_checked,
    }


def paired_index_path(entry: ExternalBamInput) -> Path:
    global PUBLIC_PATH_REPLACEMENTS
    candidates = {
        Path(os.fspath(entry.bam_path) + suffix)
        for suffix in (".bai", ".csi")
    }
    candidates.update(
        {
            entry.bam_path.with_suffix(".bai"),
            entry.bam_path.with_suffix(".csi"),
        }
    )
    for candidate in candidates:
        PUBLIC_PATH_REPLACEMENTS.append(
            (
                os.fspath(candidate.absolute()),
                f"<EXTERNAL_INDEX:{entry.workload_id}>",
            )
        )
    present = sorted(path.resolve() for path in candidates if path.is_file())
    if len(present) != 1:
        raise BenchmarkError(
            f"paired external workload {entry.workload_id!r} requires exactly "
            "one adjacent .bai or .csi index"
        )
    index_path = present[0]
    PUBLIC_PATH_REPLACEMENTS.append(
        (
            os.fspath(index_path),
            f"<EXTERNAL_INDEX:{entry.workload_id}>",
        )
    )
    return index_path


def adjacent_bam_index(bam_path: Path) -> Path:
    """Return the single adjacent BAI/CSI used by samtools for this BAM."""
    candidates = {
        Path(os.fspath(bam_path) + ".bai"),
        Path(os.fspath(bam_path) + ".csi"),
        bam_path.with_suffix(".bai"),
        bam_path.with_suffix(".csi"),
    }
    present = sorted(path for path in candidates if path.is_file())
    if len(present) != 1:
        raise BenchmarkError(
            f"benchmark input requires exactly one adjacent BAM index: "
            f"{sanitize_public_text(os.fspath(bam_path))}"
        )
    return present[0]


def preread_benchmark_input(
    *,
    samtools: Path,
    bam_input: Path,
    expected_bam_sha256: str,
    expected_index_sha256: str,
    root: Path,
) -> None:
    """Verify and pre-read the exact immutable BAM/index before a timed cell."""
    if (
        sha256_file(bam_input) != expected_bam_sha256
        or sha256_file(adjacent_bam_index(bam_input))
        != expected_index_sha256
    ):
        raise BenchmarkError("benchmark input or index changed before timing")
    run_command(
        [samtools, "view", "-c", bam_input],
        stdout_path=root / "preread-stdout.txt",
        stderr_path=root / "preread-stderr.txt",
    )
    run_command(
        [samtools, "idxstats", bam_input],
        stdout_path=root / "index-preread-stdout.txt",
        stderr_path=root / "index-preread-stderr.txt",
    )


def validate_external_bam(
    *,
    entry: ExternalBamInput,
    samtools: Path,
    validation_root: Path,
) -> dict[str, object]:
    validation_root.mkdir(parents=True, exist_ok=True)
    try:
        try:
            initial_stat = entry.bam_path.stat()
            observed_hash = sha256_file(entry.bam_path)
        except OSError as error:
            raise BenchmarkError(
                f"could not inspect external BAM {entry.workload_id!r}: "
                f"{sanitize_public_text(str(error))}"
            ) from error
        if observed_hash != entry.bam_sha256:
            raise BenchmarkError(
                f"external BAM hash mismatch for workload {entry.workload_id!r}"
            )

        quickcheck_command = [samtools, "quickcheck", "-v", entry.bam_path]
        (validation_root / "quickcheck-command.txt").write_text(
            command_text(quickcheck_command) + "\n", encoding="utf-8"
        )
        run_command(
            quickcheck_command,
            stdout_path=validation_root / "quickcheck-stdout.txt",
            stderr_path=validation_root / "quickcheck-stderr.txt",
        )
        sort_order, reference_sequences, reference_dictionary_sha256 = (
            external_header_summary(samtools=samtools, bam_path=entry.bam_path)
        )
        if sort_order != "coordinate":
            raise BenchmarkError(
                f"external BAM {entry.workload_id!r} must declare SO:coordinate"
            )
        if reference_sequences <= 0:
            raise BenchmarkError(
                f"external BAM {entry.workload_id!r} has no @SQ reference dictionary"
            )
        record_summary = validate_external_records(
            entry=entry,
            samtools=samtools,
            validation_root=validation_root,
        )

        temporary_index = validation_root / "temporary-input-index.bai"
        index_command = [
            samtools,
            "index",
            "-o",
            temporary_index,
            entry.bam_path,
        ]
        (validation_root / "index-command.txt").write_text(
            command_text(index_command) + "\n", encoding="utf-8"
        )
        try:
            run_command(
                index_command,
                stdout_path=validation_root / "index-stdout.txt",
                stderr_path=validation_root / "index-stderr.txt",
            )
            if not temporary_index.is_file() or temporary_index.stat().st_size == 0:
                raise BenchmarkError(
                    f"temporary index validation failed for workload {entry.workload_id!r}"
                )
        finally:
            temporary_index.unlink(missing_ok=True)

        paired_index_receipt: dict[str, object] | None = None
        if entry.paired:
            index_path = paired_index_path(entry)
            index_command = [samtools, "idxstats", entry.bam_path]
            (validation_root / "paired-index-command.txt").write_text(
                command_text(index_command) + "\n", encoding="utf-8"
            )
            run_command(index_command)
            paired_index_receipt = {
                "bytes": index_path.stat().st_size,
                "sha256": sha256_file(index_path),
                "path_recorded": False,
                "validation": "pass",
            }

        final_stat = entry.bam_path.stat()
        if (
            final_stat.st_size != initial_stat.st_size
            or final_stat.st_mtime_ns != initial_stat.st_mtime_ns
        ):
            raise BenchmarkError(
                f"external BAM changed during validation for workload {entry.workload_id!r}"
            )
        receipt = {
            "workload_id": entry.workload_id,
            "bytes": initial_stat.st_size,
            "sha256": observed_hash,
            "paired": entry.paired,
            "umi_length": entry.umi_length,
            "umi_separator": entry.umi_separator,
            "rationale_provided": bool(entry.rationale),
            "alias_neutrality_machine_verified": False,
            "quickcheck_status": "pass",
            "declared_sort_order": sort_order,
            "temporary_index_validation": "pass",
            "paired_index": paired_index_receipt,
            "reference_sequences": reference_sequences,
            "reference_dictionary_sha256": reference_dictionary_sha256,
            **record_summary,
            "path_recorded": False,
        }
        (validation_root / "receipt.json").write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return receipt
    finally:
        suppress_external_log_contents(validation_root)


def scan_public_evidence(output_root: Path, repository_root: Path) -> None:
    binary_suffixes = {
        ".bai",
        ".bam",
        ".class",
        ".gz",
        ".idx",
        ".jar",
        ".pack",
        ".pyc",
    }
    private_roots = (
        "/" + "mnt" + "/",
        "/" + "home" + "/",
        "/" + "Users" + "/",
    )
    exact_private_values = {
        private
        for private, replacement in PUBLIC_PATH_REPLACEMENTS
        if replacement in {"<EVIDENCE_DIR>", "<DUMI_REPOSITORY>", "<HOME>"}
        or replacement.startswith("<EXTERNAL_")
    }
    identity_tokens = {
        token
        for token in (getpass.getuser(), platform.node())
        if token and token not in {"root", "localhost"}
    }
    findings: list[str] = []

    for path in sorted(item for item in output_root.rglob("*") if item.is_file()):
        if path.suffix.lower() in binary_suffixes:
            continue
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise BenchmarkError(f"public-evidence scan could not read {path}: {error}") from error
        if b"\x00" in payload:
            continue
        text = payload.decode("utf-8", errors="replace")
        relative = path.relative_to(output_root).as_posix()
        if any(root in text for root in private_roots):
            findings.append(f"{relative}: private absolute path root")
        if any(value and value in text for value in exact_private_values):
            findings.append(f"{relative}: run-local absolute path")
        if relative.split("/", 1)[0] not in {"harness", "sources"}:
            for token in identity_tokens:
                if re.search(
                    rf"(?<![A-Za-z0-9_.-]){re.escape(token)}(?![A-Za-z0-9_.-])",
                    text,
                ):
                    findings.append(f"{relative}: local user or host token")
                    break

    if findings:
        preview = "; ".join(findings[:10])
        if len(findings) > 10:
            preview += f"; plus {len(findings) - 10} more"
        raise BenchmarkError(f"public-evidence privacy scan failed: {preview}")

    (output_root / "privacy-scan.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "rules": [
                    "no private absolute path roots",
                    "no run-local absolute paths",
                    "no local user or host tokens in generated textual evidence",
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def cleanup_external_alignment_artifacts(output_root: Path) -> None:
    for path in sorted(
        (item for item in output_root.rglob("*") if item.is_file()),
        reverse=True,
    ):
        relative_parts = path.relative_to(output_root).parts
        if relative_parts and relative_parts[0] in {"harness", "sources"}:
            continue
        if path.suffix.lower() in {
            ".bam",
            ".bai",
            ".csi",
            ".cram",
            ".crai",
            ".sam",
            ".private",
        }:
            path.unlink(missing_ok=True)


def suppress_external_log_contents(root: Path) -> list[str]:
    """Replace input-touching stdout/stderr, including on early failure."""
    redacted: list[str] = []
    if not root.is_dir():
        return redacted
    for path in sorted(item for item in root.rglob("*.txt") if item.is_file()):
        if not re.search(r"(?:^|-)(?:stdout|stderr)\.txt$", path.name):
            continue
        path.write_text(
            "External-input log content suppressed after validation or failure; "
            "commands, timings, and semantic receipts are retained.\n",
            encoding="utf-8",
        )
        redacted.append(path.as_posix())
    return redacted


def redact_external_execution_logs(output_root: Path) -> None:
    roots = [
        output_root / name
        for name in (
            "contracts",
            "input-validation",
            "oracles",
            "runs",
            "warmups",
        )
    ]
    redacted: list[str] = []
    for root in roots:
        redacted.extend(
            Path(path).relative_to(output_root).as_posix()
            for path in suppress_external_log_contents(root)
        )
    (output_root / "external-log-redaction.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "policy": (
                    "input-touching stdout and stderr are suppressed because "
                    "failure messages can contain QNAMEs"
                ),
                "redacted_files": redacted,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def sanitize_external_failure(output_root: Path) -> None:
    """Best-effort removal of private external-input derivatives on any exit."""
    try:
        redact_external_execution_logs(output_root)
    finally:
        cleanup_external_alignment_artifacts(output_root)


def require_no_alignment_artifacts(output_root: Path) -> None:
    retained = sorted(
        path.relative_to(output_root).as_posix()
        for path in output_root.rglob("*")
        if path.is_file()
        and path.relative_to(output_root).parts[0] not in {"harness", "sources"}
        and path.suffix.lower()
        in {".bam", ".bai", ".csi", ".cram", ".crai", ".sam"}
    )
    if retained:
        raise BenchmarkError(
            "external-input evidence retained alignment artifacts: "
            + ", ".join(retained[:10])
        )


def ensure_empty_output(path: Path) -> None:
    if path.exists() and not path.is_dir():
        raise BenchmarkError(f"output path exists and is not a directory: {path}")
    if path.exists() and any(path.iterdir()):
        raise BenchmarkError(f"output directory is not empty: {path}")
    path.mkdir(parents=True, exist_ok=True)


def is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def git_has_commit(git: Path, repository: Path, sha: str) -> bool:
    completed = run_command(
        [git, "-C", repository, "cat-file", "-e", f"{sha}^{{commit}}"],
        check=False,
    )
    return completed.returncode == 0


def source_repository(
    *,
    git: Path,
    local_repository: Path,
    cache_root: Path,
    cache_name: str,
    url: str,
    ref: str,
    sha: str,
) -> Path:
    if git_has_commit(git, local_repository, sha):
        return local_repository

    cache = cache_root / f"{cache_name}.git"
    cache.mkdir(parents=True, exist_ok=True)
    if not (cache / "HEAD").exists():
        run_command([git, "init", "--bare", cache])
    run_command([git, "-C", cache, "fetch", "--depth=1", url, sha])
    resolved = capture([git, "-C", cache, "rev-parse", "FETCH_HEAD"])
    if resolved != sha:
        raise BenchmarkError(
            f"{url} {ref} resolved to {resolved}, expected pinned commit {sha}"
        )
    return cache


def archive_commit(git: Path, repository: Path, sha: str, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [os.fspath(git), "-C", os.fspath(repository), "archive", "--format=tar", sha],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.stdout is None:
        raise BenchmarkError("could not read git archive output")

    destination_root = destination.resolve()
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for member in archive:
                if not (member.isfile() or member.isdir()):
                    process.kill()
                    raise BenchmarkError(
                        f"unsupported link or special entry in git archive: {member.name}"
                    )
                target = (destination / member.name).resolve()
                if target != destination_root and destination_root not in target.parents:
                    process.kill()
                    raise BenchmarkError(f"unsafe path in git archive: {member.name}")
                archive.extract(member, destination)
    finally:
        process.stdout.close()

    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    return_code = process.wait()
    if process.stderr:
        process.stderr.close()
    if return_code != 0:
        raise BenchmarkError(f"git archive failed for {sha}: {stderr.strip()}")


def verify_external_harness_commit_binding(
    *,
    git: Path,
    repository_root: Path,
    dumi_sha: str,
    harness_sources: Sequence[Path],
    harness_snapshot_root: Path,
    archived_dumi_root: Path,
    output_root: Path,
) -> dict[str, object]:
    """Bind the executable external benchmark harness to the archived commit."""
    source_names = [source.name for source in harness_sources]
    if len(source_names) != len(set(source_names)):
        raise BenchmarkError("benchmark harness snapshot names are not unique")

    files: list[dict[str, str]] = []
    for source in harness_sources:
        try:
            repository_path = source.resolve().relative_to(
                repository_root.resolve()
            )
        except ValueError as error:
            raise BenchmarkError(
                "external benchmark harness files must be inside the dUMI "
                "repository"
            ) from error
        repository_path_text = repository_path.as_posix()
        status = run_command(
            [
                git,
                "-C",
                repository_root,
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--",
                repository_path_text,
            ],
            check=False,
        )
        if status.returncode != 0:
            raise BenchmarkError(
                "could not verify external benchmark harness Git status for "
                f"{repository_path_text}"
            )
        if (status.stdout or "").strip():
            raise BenchmarkError(
                "external benchmark harness file is not tracked and clean: "
                f"{repository_path_text}"
            )

        snapshot = harness_snapshot_root / source.name
        archived = archived_dumi_root / repository_path
        if not archived.is_file():
            raise BenchmarkError(
                "external benchmark harness file is not tracked by the "
                f"archived dUMI commit: {repository_path_text}"
            )
        source_digest = sha256_file(source)
        snapshot_digest = sha256_file(snapshot)
        archived_digest = sha256_file(archived)
        if len({source_digest, snapshot_digest, archived_digest}) != 1:
            raise BenchmarkError(
                "external benchmark harness file is not byte-identical to "
                f"the archived dUMI commit: {repository_path_text}"
            )
        files.append(
            {
                "repository_path": repository_path_text,
                "snapshot_path": record_path(snapshot, output_root),
                "sha256": snapshot_digest,
            }
        )

    return {
        "status": "verified",
        "repository_url": DUMI_PUBLIC_URL,
        "commit_sha": dumi_sha,
        "files": files,
    }


def parse_dependency_lock(path: Path) -> list[dict[str, str]]:
    dependencies: list[dict[str, str]] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 3:
            raise BenchmarkError(f"malformed dependency lock line {line_number}: {raw_line}")
        filename, digest, url = fields
        if (
            filename in seen
            or "/" in filename
            or not filename.endswith(".jar")
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not url.startswith("https://")
        ):
            raise BenchmarkError(f"invalid dependency lock line {line_number}: {raw_line}")
        seen.add(filename)
        dependencies.append({"filename": filename, "sha256": digest, "url": url})
    if not dependencies:
        raise BenchmarkError(f"no dependencies found in {path}")
    return dependencies


def prepare_dependencies(
    *,
    dependencies: list[dict[str, str]],
    destination: Path,
    repository_root: Path,
    curl: Path,
) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for dependency in dependencies:
        filename = dependency["filename"]
        expected = dependency["sha256"]
        output = destination / filename
        local_cache = repository_root / "lib" / filename

        if output.exists() and sha256_file(output) != expected:
            output.unlink()
        if not output.exists() and local_cache.exists() and sha256_file(local_cache) == expected:
            shutil.copy2(local_cache, output)
        if not output.exists():
            temporary = output.with_suffix(output.suffix + ".partial")
            run_command(
                [
                    curl,
                    "--fail",
                    "--location",
                    "--retry",
                    "3",
                    "--proto",
                    "=https",
                    "--tlsv1.2",
                    "--output",
                    temporary,
                    dependency["url"],
                ]
            )
            if sha256_file(temporary) != expected:
                temporary.unlink(missing_ok=True)
                raise BenchmarkError(f"checksum mismatch for downloaded dependency {filename}")
            temporary.replace(output)
        if sha256_file(output) != expected:
            raise BenchmarkError(f"dependency checksum mismatch: {output}")
        paths.append(output)
    return paths


def compile_source(
    *,
    label: str,
    source_root: Path,
    classes_root: Path,
    dependency_paths: Sequence[Path],
    javac: Path,
    command_root: Path,
) -> dict[str, object]:
    sources = sorted((source_root / "src" / "umicollapse").rglob("*.java"))
    if not sources:
        raise BenchmarkError(f"no production Java sources found for {label}")
    classes_root.mkdir(parents=True, exist_ok=True)
    classpath = os.pathsep.join(os.fspath(path) for path in dependency_paths)
    command = [
        javac,
        "--release",
        "11",
        "-cp",
        classpath,
        "-d",
        classes_root,
        *sources,
    ]
    build_root = command_root / label
    build_root.mkdir(parents=True, exist_ok=True)
    (build_root / "command.txt").write_text(command_text(command) + "\n", encoding="utf-8")
    run_command(
        command,
        stdout_path=build_root / "stdout.txt",
        stderr_path=build_root / "stderr.txt",
    )
    return {
        "label": label,
        "source_tree_sha256": sha256_tree(source_root / "src" / "umicollapse"),
        "classes_tree_sha256": sha256_tree(classes_root),
        "source_count": len(sources),
        "command_file": str((build_root / "command.txt").relative_to(command_root.parent)),
    }


def parse_positive_list(value: str, option: str) -> list[int]:
    parts = [item.strip() for item in value.split(",")]
    if not parts or any(not item for item in parts):
        raise BenchmarkError(f"{option} must be comma-separated positive integers")
    try:
        parsed = [int(item) for item in parts]
    except ValueError as exc:
        raise BenchmarkError(f"{option} must be comma-separated integers") from exc
    if not parsed or any(item <= 0 for item in parsed):
        raise BenchmarkError(f"{option} values must all be positive")
    if len(parsed) != len(set(parsed)):
        raise BenchmarkError(f"{option} must not contain duplicate values")
    return parsed


def latin_order(items: Sequence[Implementation], row: int) -> list[Implementation]:
    """Return a cyclic Latin-square row, reversing alternate complete squares."""
    if not items:
        return []
    size = len(items)
    block = row // size
    shift = row % size
    base = list(items)
    if block % 2:
        base.reverse()
    return base[shift:] + base[:shift]


def williams_order(
    items: Sequence[Implementation], row: int
) -> list[Implementation]:
    """Return one row of an even-treatment first-order-balanced Williams design."""
    if not items:
        return []
    size = len(items)
    if size % 2:
        raise BenchmarkError(
            "Williams timing order requires an even number of treatments"
        )
    indexes = [0]
    for position in range(1, size):
        indexes.append(
            (position + 1) // 2
            if position % 2
            else size - position // 2
        )
    shift = row % size
    return [items[(index + shift) % size] for index in indexes]


def workload_timing_schedule(
    implementations: Sequence[Implementation],
    repetitions: int,
    workload_index: int,
) -> list[ScheduledCell]:
    """Materialize the predeclared randomized order for one workload."""
    order_function = (
        williams_order
        if len(implementations) % 2 == 0
        else latin_order
    )
    return [
        ScheduledCell(repetition, order_index, implementation)
        for repetition in range(1, repetitions + 1)
        for order_index, implementation in enumerate(
            order_function(
                implementations,
                repetition - 1 + workload_index,
            ),
            1,
        )
    ]


def workload_stage_schedule(
    implementations: Sequence[Implementation],
    repetitions: int,
    workload_index: int,
) -> list[tuple[str, ScheduledCell]]:
    """Build separately balanced raw and end-to-end-ready schedules."""
    raw_cells = workload_timing_schedule(
        implementations,
        repetitions,
        workload_index,
    )
    end_to_end_cells = workload_timing_schedule(
        implementations,
        repetitions,
        workload_index + 1,
    )
    return [("raw", cell) for cell in raw_cells] + [
        ("end_to_end_ready", cell) for cell in end_to_end_cells
    ]


def execute_timing_blocks(
    *,
    scheduled_cells: Sequence[tuple[str, ScheduledCell]],
    repetitions: int,
    treatments_per_block: int,
    time_cell: Callable[[str, ScheduledCell], object],
    validate_cell: Callable[[object], dict[str, object]],
) -> list[dict[str, object]]:
    """Time a complete treatment block before validating or deleting any cell."""
    rows: list[dict[str, object]] = []
    for stage in MEASURED_STAGES:
        stage_cells = [
            cell
            for scheduled_stage, cell in scheduled_cells
            if scheduled_stage == stage
        ]
        for repetition in range(1, repetitions + 1):
            block_cells = [
                cell
                for cell in stage_cells
                if cell.repetition == repetition
            ]
            if len(block_cells) != treatments_per_block:
                raise BenchmarkError(
                    f"incomplete timing block for {stage}/"
                    f"repetition {repetition}"
                )
            pending = [
                time_cell(stage, cell) for cell in block_cells
            ]
            rows.extend(validate_cell(cell) for cell in pending)
    return rows


def require_stage_scratch_capacity(
    *,
    output_root: Path,
    bam_input: Path,
    treatments_per_block: int,
    directional_oracle_record_count: int | None = None,
    directional_oracle_paired: bool = False,
    directional_oracle_umi_length: int | None = None,
    receipt_path: Path | None = None,
    directional_oracle_only: bool = False,
) -> dict[str, object]:
    """Fail early when timing and the deferred external oracle cannot fit.

    Each retained BAM is conservatively budgeted at 125% of the input. The
    end-to-end-ready worst case retains a Java intermediate and final BAM per
    treatment, plus one output-sized samtools-sort scratch allowance. External
    mode additionally budgets the two tagged BAMs and the independent
    directional oracle's count-bounded private sort/canonical streams.
    """
    if treatments_per_block <= 0:
        raise BenchmarkError("timing block must contain at least one treatment")
    if directional_oracle_record_count is not None:
        if directional_oracle_record_count <= 0:
            raise BenchmarkError(
                "directional-oracle record count must be positive"
            )
        if (
            directional_oracle_umi_length is None
            or directional_oracle_umi_length <= 0
        ):
            raise BenchmarkError(
                "directional-oracle UMI length must be positive"
            )
    elif directional_oracle_umi_length is not None:
        raise BenchmarkError(
            "directional-oracle UMI length requires a record count"
        )
    if directional_oracle_only and directional_oracle_record_count is None:
        raise BenchmarkError(
            "directional-oracle-only capacity requires a record count"
        )
    try:
        input_bytes = bam_input.stat().st_size
        available_bytes = shutil.disk_usage(output_root).free
    except OSError as error:
        raise BenchmarkError(
            "could not determine scratch capacity before raw timing"
        ) from error
    estimated_output_bytes_per_cell = (input_bytes * 5 + 3) // 4
    retained_block_outputs = treatments_per_block * 2
    sort_scratch_outputs = 1
    timing_peak_stage_bytes = estimated_output_bytes_per_cell * (
        retained_block_outputs + sort_scratch_outputs
    )
    directional_oracle_peak_stage_bytes = 0
    directional_source_record_key_bytes = 0
    directional_tagged_record_key_bytes_each = 0
    directional_membership_canonical_bytes_each = 0
    directional_rooted_canonical_bytes_each = 0
    directional_alignment_umi_aggregate_bytes_each = 0
    directional_retained_canonical_bytes = 0
    directional_active_persistent_bytes = 0
    directional_concurrent_sort_destination_merge_bytes = 0
    directional_sort_destination_merge_bytes = 0
    directional_sort_buffer_memory_bytes = 0
    directional_tagged_bam_bytes_each = 0
    directional_tagged_bam_allowance_bytes = 0
    directional_alignment_key_bytes = 0
    if directional_oracle_record_count is not None:
        assert directional_oracle_umi_length is not None
        record_count = directional_oracle_record_count
        umi_length = directional_oracle_umi_length
        directional_alignment_key_bytes = (
            25 if directional_oracle_paired else 17
        )
        directional_source_record_key_bytes = record_count * (
            directional_alignment_key_bytes + umi_length + 2
        )
        directional_tagged_record_key_bytes_each = record_count * (
            directional_alignment_key_bytes + 2 * umi_length + 12
        )
        directional_membership_canonical_bytes_each = record_count * (
            directional_alignment_key_bytes + umi_length + 13
        )
        directional_rooted_canonical_bytes_each = record_count * (
            directional_alignment_key_bytes + 2 * umi_length + 14
        )
        directional_alignment_umi_aggregate_bytes_each = (
            directional_membership_canonical_bytes_each
        )
        # The source oracle, dUMI-off, and canonical upstream each retain a
        # membership and rooted-membership canonical stream until comparison.
        directional_retained_canonical_bytes = 3 * (
            directional_membership_canonical_bytes_each
            + directional_rooted_canonical_bytes_each
        )
        # At the final tagged implementation, the oracle and upstream
        # membership/root streams remain while the active record-key and its
        # two final canonical destinations are materialized.
        directional_active_persistent_bytes = (
            directional_tagged_record_key_bytes_each
            + directional_retained_canonical_bytes
            + directional_alignment_umi_aggregate_bytes_each
        )
        # Membership, rooted-membership, and alignment/UMI-frequency streams
        # are sorted concurrently. Count both final destinations and
        # equal-sized merge scratch; persistent destinations are already in
        # the active-persistent term above.
        directional_concurrent_sort_destination_merge_bytes = 2 * (
            directional_membership_canonical_bytes_each
            + directional_rooted_canonical_bytes_each
            + directional_alignment_umi_aggregate_bytes_each
        )
        directional_sort_destination_merge_bytes = (
            directional_membership_canonical_bytes_each
            + directional_rooted_canonical_bytes_each
            + directional_alignment_umi_aggregate_bytes_each
        )
        directional_sort_buffer_memory_bytes = 3 * 256 * 1024 * 1024
        directional_tagged_bam_bytes_each = max(
            estimated_output_bytes_per_cell
            + record_count * (umi_length + 32),
            input_bytes + record_count * (2 * umi_length + 64),
        )
        directional_tagged_bam_allowance_bytes = (
            2 * directional_tagged_bam_bytes_each
        )
        directional_oracle_peak_stage_bytes = (
            directional_tagged_bam_allowance_bytes
            + directional_active_persistent_bytes
            + directional_sort_destination_merge_bytes
        )
    peak_stage_output_bytes = (
        directional_oracle_peak_stage_bytes
        if directional_oracle_only
        else max(
            timing_peak_stage_bytes,
            directional_oracle_peak_stage_bytes,
        )
    )
    headroom_bytes = max(
        256 * 1024 * 1024,
        (peak_stage_output_bytes + 9) // 10,
    )
    required_available_bytes = peak_stage_output_bytes + headroom_bytes
    receipt: dict[str, object] = {
        "status": (
            "pass"
            if available_bytes >= required_available_bytes
            else "insufficient"
        ),
        "scope": (
            "deferred-directional-oracle-only"
            if directional_oracle_only
            else (
                "complete-timing-block-and-deferred-directional-oracle"
                if directional_oracle_record_count is not None
                else "complete-end-to-end-ready-repetition-block"
            )
        ),
        "treatments_per_repetition_block": treatments_per_block,
        "retained_block_output_allowances": retained_block_outputs,
        "samtools_sort_scratch_allowances": sort_scratch_outputs,
        "input_bam_bytes": input_bytes,
        "estimated_output_bytes_per_cell": estimated_output_bytes_per_cell,
        "timing_peak_stage_bytes": timing_peak_stage_bytes,
        "directional_oracle_applicable": (
            directional_oracle_record_count is not None
        ),
        "directional_oracle_record_count_upper_bound": (
            directional_oracle_record_count or 0
        ),
        "directional_oracle_alignment_key_bytes_per_record": (
            directional_alignment_key_bytes
        ),
        "directional_oracle_source_record_key_bytes": (
            directional_source_record_key_bytes
        ),
        "directional_oracle_tagged_record_key_bytes_each": (
            directional_tagged_record_key_bytes_each
        ),
        "directional_oracle_membership_canonical_bytes_each": (
            directional_membership_canonical_bytes_each
        ),
        "directional_oracle_rooted_canonical_bytes_each": (
            directional_rooted_canonical_bytes_each
        ),
        "directional_oracle_alignment_umi_aggregate_bytes_each": (
            directional_alignment_umi_aggregate_bytes_each
        ),
        "directional_oracle_retained_canonical_bytes": (
            directional_retained_canonical_bytes
        ),
        "directional_oracle_active_persistent_bytes": (
            directional_active_persistent_bytes
        ),
        "directional_oracle_concurrent_sort_destination_merge_bytes": (
            directional_concurrent_sort_destination_merge_bytes
        ),
        "directional_oracle_sort_destination_merge_bytes": (
            directional_sort_destination_merge_bytes
        ),
        "directional_oracle_concurrent_sort_buffer_memory_bytes": (
            directional_sort_buffer_memory_bytes
        ),
        "directional_oracle_tagged_bam_allowance_bytes": (
            directional_tagged_bam_allowance_bytes
        ),
        "directional_oracle_tagged_bam_bytes_each": (
            directional_tagged_bam_bytes_each
        ),
        "directional_oracle_peak_stage_bytes": (
            directional_oracle_peak_stage_bytes
        ),
        "peak_stage_output_bytes": peak_stage_output_bytes,
        "headroom_bytes": headroom_bytes,
        "required_available_bytes": required_available_bytes,
        "available_bytes": available_bytes,
    }
    if receipt_path is not None:
        temporary = receipt_path.with_name(receipt_path.name + ".tmp")
        try:
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            temporary.replace(receipt_path)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise BenchmarkError(
                "could not record scratch-capacity preflight receipt"
            ) from error
    if available_bytes < required_available_bytes:
        raise BenchmarkError(
            "insufficient scratch capacity for the timing and deferred "
            "directional-oracle stages: "
            f"need at least {required_available_bytes} available bytes, "
            f"found {available_bytes}"
        )
    return receipt


def parse_time_metrics(path: Path) -> tuple[str, str, str, str, str, str]:
    if not path.exists():
        return ("", "", "", "", "", "")
    candidates = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    for line in reversed(candidates):
        fields = line.split("\t")
        if len(fields) == 6:
            return tuple(fields)  # type: ignore[return-value]
    return ("", "", "", "", "", "")


def record_path(path: Path, output_root: Path) -> str:
    try:
        return path.relative_to(output_root).as_posix()
    except ValueError:
        return os.fspath(path)


def validate_design_completion(design_path: Path, measurement_path: Path) -> None:
    key_fields = (
        "run_id",
        "workload",
        "scale",
        "stage",
        "implementation",
        "mode",
        "repetition",
        "order",
    )

    def rows(path: Path) -> list[tuple[str, ...]]:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if not reader.fieldnames or any(field not in reader.fieldnames for field in key_fields):
                raise BenchmarkError(f"completion-gate fields are missing from {path}")
            return [tuple((row.get(field) or "") for field in key_fields) for row in reader]

    expected = rows(design_path)
    observed = rows(measurement_path)
    if len(expected) != len(set(expected)):
        raise BenchmarkError("benchmark design contains duplicate cells")
    if len(observed) != len(set(observed)):
        raise BenchmarkError("measurements contain duplicate scheduled cells")
    missing = sorted(set(expected) - set(observed))
    extra = sorted(set(observed) - set(expected))
    if missing or extra:
        raise BenchmarkError(
            f"benchmark completion gate failed: {len(missing)} missing, {len(extra)} extra cells"
        )
    if expected != observed:
        raise BenchmarkError(
            "benchmark completion gate failed: measurement order differs from design"
        )


def inspect_output(
    *,
    checker: Path,
    python: Path,
    samtools: Path,
    output: Path,
    temporary_root: Path,
    reference: Path | None = None,
    reference_canonical: Path | None = None,
    reference_canonical_receipt: Path | None = None,
    reference_canonical_sha256: str | None = None,
    canonical_output: Path | None = None,
    canonical_receipt_output: Path | None = None,
    alignment_group_mode: str = "single-end",
) -> dict[str, object]:
    temporary_root.mkdir(parents=True, exist_ok=True)
    command = [
        python,
        checker,
        "--samtools",
        samtools,
        "--tmpdir",
        temporary_root,
        "--alignment-group-mode",
        alignment_group_mode,
    ]
    if reference is not None:
        command.extend(["--reference", reference])
    if reference_canonical is not None:
        command.extend(["--reference-canonical", reference_canonical])
    if reference_canonical_receipt is not None:
        command.extend(
            ["--reference-canonical-receipt", reference_canonical_receipt]
        )
    if reference_canonical_sha256 is not None:
        command.extend(
            ["--reference-canonical-sha256", reference_canonical_sha256]
        )
    if canonical_output is not None:
        command.extend(["--canonical-output", canonical_output])
    if canonical_receipt_output is not None:
        command.extend(
            ["--canonical-receipt-output", canonical_receipt_output]
        )
    command.append(output)
    completed = run_command(command, check=False)
    try:
        result = json.loads(completed.stdout or "")
    except json.JSONDecodeError as exc:
        raise BenchmarkError(
            "semantic checker returned invalid JSON for "
            f"{sanitize_public_text(os.fspath(output))}"
        ) from exc
    if completed.returncode not in {0, 1}:
        detail = sanitize_public_text((completed.stderr or "").strip())
        raise BenchmarkError(
            f"semantic checker failed for {sanitize_public_text(os.fspath(output))}"
            + (f": {detail}" if detail else "")
        )
    if result.get("quickcheck_status") != "pass":
        raise BenchmarkError(f"samtools quickcheck failed for {output}")
    result["exact_oracle_match"] = (
        reference is None
        or (
            result.get("record_equivalent") is True
            and result.get("reference_dictionary_equivalent") is True
            and result.get("read_group_dictionary_equivalent") is True
            and result.get("alignment_group_output_count_equivalent") is True
            and completed.returncode == 0
        )
    )
    if ACTIVE_OUTPUT_ROOT is not None:
        result["output_file"] = record_path(output, ACTIVE_OUTPUT_ROOT)
        if result.get("reference_file"):
            result["reference_file"] = sanitize_public_text(
                str(result["reference_file"])
            )
    result["output_bytes"] = output.stat().st_size
    result["output_sha256"] = sha256_file(output)
    return result


def build_java_bam_command(
    *,
    java: Path,
    jvm_options: Sequence[str],
    java_tmp: Path,
    classes_root: Path,
    common_classpath: str,
    bam_input: Path,
    output: Path,
    workload: Workload,
    source_key: str,
    streaming_mode: str | None,
    tag_clusters: bool = False,
) -> list[os.PathLike[str] | str]:
    command: list[os.PathLike[str] | str] = [
        java,
        *jvm_options,
        f"-Djava.io.tmpdir={java_tmp}",
        "-cp",
        os.pathsep.join([os.fspath(classes_root), common_classpath]),
        "umicollapse.main.Main",
        "bam",
        "-i",
        bam_input,
        "-o",
        output,
        "-u",
        str(workload.umi_length),
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
    ]
    if workload.external_input is not None:
        command.extend(
            [
                "--umi-sep",
                (
                    workload.umi_separator
                    if source_key == "dumi"
                    else java_pattern_quote(workload.umi_separator)
                ),
            ]
        )
    if workload.paired:
        command.append("--paired")
    if tag_clusters:
        command.append("--tag")
    if streaming_mode is not None:
        command.extend(["--streaming-mode", streaming_mode])
    return command


def build_end_to_end_ready_command(
    *,
    java_command: Sequence[os.PathLike[str] | str],
    raw_sort_order: str,
    java_output: Path,
    final_output: Path,
    samtools: Path,
) -> list[str]:
    """Time fresh deduplication and every route-required readiness operation."""
    java_shell = shlex.join([os.fspath(item) for item in java_command])
    if raw_sort_order == "unsorted":
        readiness_shell = (
            f"{shlex.quote(os.fspath(samtools))} sort -o "
            f"{shlex.quote(os.fspath(final_output))} "
            f"{shlex.quote(os.fspath(java_output))} && "
            f"{shlex.quote(os.fspath(samtools))} index "
            f"{shlex.quote(os.fspath(final_output))}"
        )
    elif raw_sort_order == "coordinate":
        if java_output != final_output:
            raise BenchmarkError(
                "coordinate end-to-end-ready Java output must be the final output"
            )
        readiness_shell = (
            f"{shlex.quote(os.fspath(samtools))} index "
            f"{shlex.quote(os.fspath(final_output))}"
        )
    else:
        raise BenchmarkError(
            f"unsupported raw sort order for end-to-end-ready stage: "
            f"{raw_sort_order!r}"
        )
    return ["bash", "-c", f"{java_shell} && {readiness_shell}"]


def snapshot_external_input(
    *,
    entry: ExternalBamInput,
    validation_receipt: dict[str, object],
    destination_root: Path,
) -> tuple[Path, dict[str, object]]:
    """Create a verified private copy so timed commands never reopen source bytes."""
    destination_root.mkdir(parents=True, exist_ok=True)
    destination = destination_root / "input.private.bam"
    source_stat = entry.bam_path.stat()
    try:
        shutil.copyfile(entry.bam_path, destination)
        copied_hash = sha256_file(destination)
        final_source_stat = entry.bam_path.stat()
        if (
            copied_hash != entry.bam_sha256
            or sha256_file(entry.bam_path) != entry.bam_sha256
            or source_stat.st_size != final_source_stat.st_size
            or source_stat.st_mtime_ns != final_source_stat.st_mtime_ns
        ):
            raise BenchmarkError(
                f"external BAM changed while creating the private timing snapshot "
                f"for workload {entry.workload_id!r}"
            )
        os.chmod(destination, 0o400)

        index_receipt: dict[str, object] | None = None
        if entry.paired:
            source_index = paired_index_path(entry)
            expected_index = validation_receipt.get("paired_index")
            if not isinstance(expected_index, dict):
                raise BenchmarkError(
                    f"paired index validation receipt is missing for "
                    f"workload {entry.workload_id!r}"
                )
            suffix = source_index.suffix.lower()
            destination_index = Path(os.fspath(destination) + suffix)
            index_source_stat = source_index.stat()
            shutil.copyfile(source_index, destination_index)
            copied_index_hash = sha256_file(destination_index)
            final_index_stat = source_index.stat()
            if (
                copied_index_hash != expected_index.get("sha256")
                or sha256_file(source_index) != expected_index.get("sha256")
                or index_source_stat.st_size != final_index_stat.st_size
                or index_source_stat.st_mtime_ns != final_index_stat.st_mtime_ns
            ):
                raise BenchmarkError(
                    f"external BAM index changed while creating the private timing "
                    f"snapshot for workload {entry.workload_id!r}"
                )
            os.chmod(destination_index, 0o400)
            index_receipt = {
                "bytes": destination_index.stat().st_size,
                "sha256": copied_index_hash,
                "format": suffix.removeprefix("."),
                "path_recorded": False,
            }
        return destination, {
            "kind": "verified_private_copy",
            "bytes": destination.stat().st_size,
            "sha256": copied_hash,
            "read_only": True,
            "path_recorded": False,
            "paired_index": index_receipt,
            "retained_after_sealing": False,
        }
    except BaseException:
        for candidate in destination_root.glob("input.private.bam*"):
            candidate.unlink(missing_ok=True)
        raise


def verify_external_timing_snapshot(
    *,
    entry: ExternalBamInput,
    snapshot_bam: Path,
    validation_receipt: dict[str, object],
) -> None:
    """Verify both immutable timing copies and their original source bytes."""
    snapshot_receipt = validation_receipt.get("private_timing_snapshot")
    if not isinstance(snapshot_receipt, dict):
        raise BenchmarkError(
            f"private timing snapshot receipt is missing for workload "
            f"{entry.workload_id!r}"
        )
    if (
        not snapshot_bam.is_file()
        or snapshot_bam.stat().st_size != snapshot_receipt.get("bytes")
        or sha256_file(snapshot_bam) != snapshot_receipt.get("sha256")
    ):
        raise BenchmarkError(
            f"private BAM snapshot changed during timing for workload "
            f"{entry.workload_id!r}"
        )
    if (
        not entry.bam_path.is_file()
        or entry.bam_path.stat().st_size != validation_receipt.get("bytes")
        or sha256_file(entry.bam_path) != entry.bam_sha256
    ):
        raise BenchmarkError(
            f"external BAM changed during timing for workload "
            f"{entry.workload_id!r}"
        )

    timing_index_receipt = snapshot_receipt.get("timing_index")
    if not isinstance(timing_index_receipt, dict):
        raise BenchmarkError(
            f"private timing index receipt is missing for workload "
            f"{entry.workload_id!r}"
        )
    timing_index_format = timing_index_receipt.get("format")
    if timing_index_format not in {"bai", "csi"}:
        raise BenchmarkError(
            f"private timing index format is invalid for workload "
            f"{entry.workload_id!r}"
        )
    timing_index = Path(
        os.fspath(snapshot_bam) + f".{timing_index_format}"
    )
    if (
        not timing_index.is_file()
        or timing_index.stat().st_size != timing_index_receipt.get("bytes")
        or sha256_file(timing_index) != timing_index_receipt.get("sha256")
    ):
        raise BenchmarkError(
            f"private BAM timing index changed during timing for workload "
            f"{entry.workload_id!r}"
        )

    if not entry.paired:
        if snapshot_receipt.get("paired_index") is not None:
            raise BenchmarkError(
                f"unexpected private index receipt for single-end workload "
                f"{entry.workload_id!r}"
            )
        return

    private_index_receipt = snapshot_receipt.get("paired_index")
    source_index_receipt = validation_receipt.get("paired_index")
    if not isinstance(private_index_receipt, dict) or not isinstance(
        source_index_receipt, dict
    ):
        raise BenchmarkError(
            f"paired index receipt is missing for workload "
            f"{entry.workload_id!r}"
        )
    index_format = private_index_receipt.get("format")
    if index_format not in {"bai", "csi"}:
        raise BenchmarkError(
            f"private paired index format is invalid for workload "
            f"{entry.workload_id!r}"
        )
    private_index = Path(os.fspath(snapshot_bam) + f".{index_format}")
    if (
        not private_index.is_file()
        or private_index.stat().st_size != private_index_receipt.get("bytes")
        or sha256_file(private_index) != private_index_receipt.get("sha256")
    ):
        raise BenchmarkError(
            f"private paired BAM index changed during timing for workload "
            f"{entry.workload_id!r}"
        )
    source_index = paired_index_path(entry)
    if (
        source_index.stat().st_size != source_index_receipt.get("bytes")
        or sha256_file(source_index) != source_index_receipt.get("sha256")
    ):
        raise BenchmarkError(
            f"paired BAM index changed during timing for workload "
            f"{entry.workload_id!r}"
        )


def observed_execution_route(
    *,
    stdout_path: Path,
    stderr_path: Path,
    sort_order: object,
    implementation_name: str,
    requested_mode: str,
    paired: bool,
    context: str,
) -> str:
    stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
    stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
    marker_seen = STREAMING_MARKER in stdout_text
    fallback_seen = STREAMING_FALLBACK_MARKER in stderr_text
    observed_sort_order = str(sort_order)

    if implementation_name != "dumi":
        if marker_seen or fallback_seen or observed_sort_order != "coordinate":
            raise BenchmarkError(f"unexpected upstream execution route in {context}")
        return "coordinate"
    if requested_mode == "off":
        if marker_seen or fallback_seen or observed_sort_order != "coordinate":
            raise BenchmarkError(f"unexpected dUMI off execution route in {context}")
        return "off"
    if fallback_seen:
        if requested_mode != "auto" or not marker_seen or observed_sort_order != "coordinate":
            raise BenchmarkError(f"invalid dUMI streaming fallback receipt in {context}")
        return "fallback-off"
    if marker_seen:
        if requested_mode not in {"on", "auto"} or observed_sort_order != "unsorted":
            raise BenchmarkError(f"invalid dUMI streaming receipt in {context}")
        return "streaming"
    if requested_mode == "auto" and paired and observed_sort_order == "coordinate":
        return "off-ineligible"
    if requested_mode == "auto" and observed_sort_order == "coordinate":
        # A declared-coordinate single-end input can be rejected before the
        # streaming writer is selected by a future eligibility contract.
        return "off-ineligible"
    raise BenchmarkError(
        f"could not classify dUMI execution route in {context}: "
        f"mode={requested_mode!r}, sort_order={observed_sort_order!r}"
    )


def validate_external_route_contract(
    *,
    workload: Workload,
    implementation_name: str,
    requested_mode: str,
    observed_route: str,
    context: str,
) -> None:
    """Reconcile measured routing with the workload's untimed eligibility probe."""
    if workload.external_input is None:
        return
    if implementation_name != "dumi":
        allowed = {"coordinate"}
    elif requested_mode == "off":
        allowed = {"off"}
    elif requested_mode == "on":
        if workload.streaming_on_eligible is not True:
            raise BenchmarkError(
                f"forced-on cell was scheduled without a passing eligibility "
                f"contract in {context}"
            )
        allowed = {"streaming"}
    elif requested_mode == "auto":
        allowed = (
            {"streaming", "fallback-off"}
            if workload.streaming_on_eligible is True
            else {"off-ineligible", "fallback-off"}
        )
    else:
        raise BenchmarkError(
            f"unknown external dUMI route request {requested_mode!r} in {context}"
        )
    if observed_route not in allowed:
        raise BenchmarkError(
            f"external execution route {observed_route!r} contradicts the "
            f"forced-on eligibility contract in {context}"
        )


def cross_implementation_oracle_receipt(
    *,
    candidate: dict[str, object],
    reference: dict[str, object],
    context: str,
) -> dict[str, object]:
    record_counts_equal = int(candidate["output_records"]) == int(
        reference["output_records"]
    )
    alignment_group_record_counts_equal = int(
        candidate["alignment_group_output_records"]
    ) == int(
        reference["alignment_group_output_records"]
    )
    excluded_unmapped_counts_equal = int(
        candidate["alignment_group_records_excluded_unmapped"]
    ) == int(reference["alignment_group_records_excluded_unmapped"])
    excluded_second_counts_equal = int(
        candidate["alignment_group_records_excluded_second_of_pair"]
    ) == int(reference["alignment_group_records_excluded_second_of_pair"])
    ordered_sq_equal = (
        candidate.get("reference_dictionary_equivalent") is True
    )
    ordered_rg_equal = (
        candidate.get("read_group_dictionary_equivalent") is True
    )
    alignment_group_output_counts_equal = (
        candidate.get("alignment_group_output_count_equivalent") is True
    )
    diagnostic_match = (
        record_counts_equal
        and alignment_group_record_counts_equal
        and excluded_unmapped_counts_equal
        and excluded_second_counts_equal
        and ordered_sq_equal
        and ordered_rg_equal
        and alignment_group_output_counts_equal
    )
    return {
        "status": "match" if diagnostic_match else "difference",
        "scope": "diagnostic-only",
        "exact_match": candidate.get("record_equivalent") is True,
        "output_count_match": record_counts_equal,
        "alignment_group_output_count_match": (
            alignment_group_output_counts_equal
        ),
        "record_counts_equal": record_counts_equal,
        "alignment_group_output_record_counts_equal": (
            alignment_group_record_counts_equal
        ),
        "excluded_unmapped_counts_equal": excluded_unmapped_counts_equal,
        "excluded_second_of_pair_counts_equal": excluded_second_counts_equal,
        "ordered_sq_equal": ordered_sq_equal,
        "ordered_rg_equal": ordered_rg_equal,
        "alignment_group_output_count_multiset_equal": (
            alignment_group_output_counts_equal
        ),
    }



def validate_directional_oracle_receipt(
    *,
    receipt: dict[str, object],
    return_code: int,
    workload: Workload,
    source: Path,
    canonical_upstream: Path,
    dumi_off: Path,
    directional_checker: Path,
    partition_checker: Path,
) -> None:
    """Strictly bind a directional-oracle receipt to its staged inputs."""
    if workload.external_input is None:
        raise BenchmarkError(
            "directional-oracle receipt is only defined for external workloads"
        )
    if set(receipt) != {
        "schema",
        "version",
        "methods",
        "configuration",
        "gate",
        "diagnostics",
        "source_oracle",
        "canonical_upstream",
        "dumi_off",
        "temporary_storage",
        "provenance",
    }:
        raise BenchmarkError(
            "directional-oracle receipt has unexpected top-level fields"
        )
    if (
        receipt.get("schema") != DIRECTIONAL_ORACLE_SCHEMA
        or receipt.get("version") != DIRECTIONAL_ORACLE_SCHEMA_VERSION
        or isinstance(receipt.get("version"), bool)
        or receipt.get("methods") != DIRECTIONAL_ORACLE_METHODS
    ):
        raise BenchmarkError(
            "directional-oracle receipt schema or methods are invalid"
        )

    separator = workload.umi_separator.encode("ascii")
    if receipt.get("configuration") != {
        "mode": "paired" if workload.paired else "single-end",
        "umi_length": workload.umi_length,
        "umi_separator_bytes": len(separator),
        "umi_separator_sha256": hashlib.sha256(separator).hexdigest(),
        "edit_distance": 1,
        "percentage_decimal": "0.5",
        "percentage_binary32_hex": "3f000000",
        "remove_unpaired": False,
        "remove_chimeric": False,
        "sort_buffer_size": "256M",
    }:
        raise BenchmarkError(
            "directional-oracle receipt configuration does not match the workload"
        )

    gate = receipt.get("gate")
    if (
        not isinstance(gate, dict)
        or set(gate) != set(DIRECTIONAL_ORACLE_GATE_FIELDS)
        or not all(
            isinstance(gate.get(field), bool)
            for field in DIRECTIONAL_ORACLE_GATE_FIELDS
        )
    ):
        raise BenchmarkError("directional-oracle gate fields are invalid")
    expected_gate_pass = all(
        gate[field]
        for field in DIRECTIONAL_ORACLE_GATE_FIELDS
        if field != "directional_oracle_gate_pass"
    )
    if (
        gate["directional_oracle_gate_pass"] is not expected_gate_pass
        or return_code not in {0, 1}
        or ((return_code == 0) is not expected_gate_pass)
    ):
        raise BenchmarkError(
            "directional-oracle gate contradicts its receipt or exit status"
        )

    diagnostics = receipt.get("diagnostics")
    if (
        not isinstance(diagnostics, dict)
        or set(diagnostics) != set(DIRECTIONAL_ORACLE_DIAGNOSTIC_FIELDS)
        or not all(
            isinstance(diagnostics.get(field), bool)
            for field in DIRECTIONAL_ORACLE_DIAGNOSTIC_FIELDS
        )
    ):
        raise BenchmarkError(
            "directional-oracle diagnostic fields are invalid"
        )

    metric_fields = set(DIRECTIONAL_ORACLE_METRIC_COUNT_FIELDS) | set(
        DIRECTIONAL_ORACLE_METRIC_SHA256_FIELDS
    )
    for label, staged_input in {
        "source_oracle": source,
        "canonical_upstream": canonical_upstream,
        "dumi_off": dumi_off,
    }.items():
        metrics = receipt.get(label)
        if (
            not isinstance(metrics, dict)
            or set(metrics) != metric_fields
            or not all(
                isinstance(metrics.get(field), int)
                and not isinstance(metrics.get(field), bool)
                and int(metrics[field]) >= 0
                for field in DIRECTIONAL_ORACLE_METRIC_COUNT_FIELDS
            )
            or not all(
                isinstance(metrics.get(field), str)
                and SHA256_HEX.fullmatch(str(metrics[field])) is not None
                for field in DIRECTIONAL_ORACLE_METRIC_SHA256_FIELDS
            )
        ):
            raise BenchmarkError(
                f"directional-oracle {label} metrics are invalid"
            )
        if (
            metrics["records"] != metrics["eligible_records"]
            or int(metrics["eligible_records"]) <= 0
            or int(metrics["input_bytes"]) != staged_input.stat().st_size
            or metrics["input_sha256"] != sha256_file(staged_input)
        ):
            raise BenchmarkError(
                f"directional-oracle {label} metrics do not match the staged input"
            )
    source_metrics = receipt["source_oracle"]
    upstream_metrics = receipt["canonical_upstream"]
    dumi_metrics = receipt["dumi_off"]
    assert isinstance(source_metrics, dict)
    assert isinstance(upstream_metrics, dict)
    assert isinstance(dumi_metrics, dict)

    def receipt_equality(
        left: dict[str, object],
        right: dict[str, object],
        *,
        count_field: str,
        digest_field: str,
    ) -> bool:
        return (
            left[count_field] == right[count_field]
            and left[digest_field] == right[digest_field]
        )

    if not all(
        receipt_equality(
            tagged,
            source_metrics,
            count_field="alignment_umi_frequency_multiset_bytes",
            digest_field="alignment_umi_frequency_multiset_sha256",
        )
        for tagged in (upstream_metrics, dumi_metrics)
    ):
        raise BenchmarkError(
            "directional-oracle tagged outputs do not preserve the exact "
            "source alignment/UMI frequency multiset"
        )

    expected_gate_evidence = {
        "dumi_off_oracle_partition_equivalent": receipt_equality(
            dumi_metrics,
            source_metrics,
            count_field="membership_partition_bytes",
            digest_field="membership_partition_sha256",
        ),
        "dumi_off_oracle_root_assignment_equivalent": receipt_equality(
            dumi_metrics,
            source_metrics,
            count_field="rooted_partition_bytes",
            digest_field="rooted_partition_sha256",
        ),
        "dumi_off_source_reference_dictionary_equivalent": receipt_equality(
            dumi_metrics,
            source_metrics,
            count_field="reference_sequences",
            digest_field="reference_dictionary_sha256",
        ),
        "dumi_off_source_read_group_dictionary_equivalent": receipt_equality(
            dumi_metrics,
            source_metrics,
            count_field="read_groups",
            digest_field="read_group_dictionary_sha256",
        ),
    }
    expected_diagnostic_evidence = {
        "canonical_upstream_oracle_partition_equivalent": receipt_equality(
            upstream_metrics,
            source_metrics,
            count_field="membership_partition_bytes",
            digest_field="membership_partition_sha256",
        ),
        "canonical_upstream_oracle_root_assignment_equivalent": (
            receipt_equality(
                upstream_metrics,
                source_metrics,
                count_field="rooted_partition_bytes",
                digest_field="rooted_partition_sha256",
            )
        ),
        "canonical_upstream_dumi_off_partition_equivalent": (
            receipt_equality(
                upstream_metrics,
                dumi_metrics,
                count_field="membership_partition_bytes",
                digest_field="membership_partition_sha256",
            )
        ),
        "canonical_upstream_dumi_off_root_assignment_equivalent": (
            receipt_equality(
                upstream_metrics,
                dumi_metrics,
                count_field="rooted_partition_bytes",
                digest_field="rooted_partition_sha256",
            )
        ),
        "canonical_upstream_source_reference_dictionary_equivalent": (
            receipt_equality(
                upstream_metrics,
                source_metrics,
                count_field="reference_sequences",
                digest_field="reference_dictionary_sha256",
            )
        ),
        "canonical_upstream_source_read_group_dictionary_equivalent": (
            receipt_equality(
                upstream_metrics,
                source_metrics,
                count_field="read_groups",
                digest_field="read_group_dictionary_sha256",
            )
        ),
    }
    if any(
        gate[field] is not expected
        for field, expected in expected_gate_evidence.items()
    ):
        raise BenchmarkError(
            "directional-oracle gate booleans contradict receipt evidence"
        )
    if any(
        diagnostics[field] is not expected
        for field, expected in expected_diagnostic_evidence.items()
    ):
        raise BenchmarkError(
            "directional-oracle diagnostics contradict receipt evidence"
        )
    if gate["directional_oracle_gate_pass"] is True and any(
        dumi_metrics[field] != source_metrics[field]
        for field in (
            "records",
            "alignment_groups",
            "clusters",
            "umi_memberships",
        )
    ):
        raise BenchmarkError(
            "directional-oracle passing gate has inconsistent dUMI/source "
            "aggregate metrics"
        )

    if source_metrics["input_sha256"] != workload.external_input.bam_sha256:
        raise BenchmarkError(
            "directional-oracle source hash does not match the verified workload"
        )

    temporary_storage = receipt.get("temporary_storage")
    if (
        not isinstance(temporary_storage, dict)
        or set(temporary_storage)
        != {
            "persistent_stage_peak_upper_bound_bytes",
            "sort_merge_storage_note",
        }
        or not isinstance(
            temporary_storage.get("persistent_stage_peak_upper_bound_bytes"),
            int,
        )
        or isinstance(
            temporary_storage.get("persistent_stage_peak_upper_bound_bytes"),
            bool,
        )
        or int(
            temporary_storage["persistent_stage_peak_upper_bound_bytes"]
        )
        < 0
        or not isinstance(
            temporary_storage.get("sort_merge_storage_note"), str
        )
        or not str(temporary_storage["sort_merge_storage_note"]).strip()
    ):
        raise BenchmarkError(
            "directional-oracle temporary-storage receipt is invalid"
        )
    provenance = receipt.get("provenance")
    if (
        not isinstance(provenance, dict)
        or set(provenance)
        != {
            "helper_sha256",
            "partition_checker_sha256",
            "private_streams_retained",
        }
        or provenance.get("helper_sha256")
        != sha256_file(directional_checker)
        or provenance.get("partition_checker_sha256")
        != sha256_file(partition_checker)
        or provenance.get("private_streams_retained") is not False
    ):
        raise BenchmarkError(
            "directional-oracle helper provenance is invalid"
        )


def validate_pairwise_cluster_diagnostic_receipt(
    *,
    receipt: dict[str, object],
    return_code: int,
    workload: Workload,
    directional_receipt: dict[str, object],
) -> None:
    """Validate the legacy pairwise receipt without requiring equivalence."""
    configuration = receipt.get("configuration")
    temporary_storage = receipt.get("temporary_storage")
    separator = workload.umi_separator.encode("ascii")
    boolean_fields = (
        "partition_equivalent",
        "reference_dictionary_equivalent",
        "read_group_dictionary_equivalent",
        "equivalent",
    )
    count_fields = (
        "input_records",
        "eligible_records",
        "excluded_unmapped",
        "excluded_second_of_pair",
        "excluded_unpaired",
        "excluded_mate_unmapped",
        "excluded_chimeric",
        "alignment_groups",
        "clusters",
        "umi_memberships",
        "max_umi_memberships_per_cluster",
        "record_key_bytes",
        "canonical_partition_bytes",
        "reference_sequences",
        "read_groups",
    )
    digest_fields = (
        "partition_cluster_multiset_sha256",
        "reference_dictionary_sha256",
        "read_group_dictionary_sha256",
    )
    sides = (receipt.get("left"), receipt.get("right"))
    component_match = (
        receipt.get("partition_equivalent") is True
        and receipt.get("reference_dictionary_equivalent") is True
        and receipt.get("read_group_dictionary_equivalent") is True
    )
    if (
        set(receipt)
        != {
            "schema",
            "partition_fingerprint_version",
            "equivalent",
            "partition_equivalent",
            "reference_dictionary_equivalent",
            "read_group_dictionary_equivalent",
            "configuration",
            "left",
            "right",
            "temporary_storage",
        }
        or receipt.get("schema") != "dumi-cluster-partition-check-v1"
        or receipt.get("partition_fingerprint_version")
        != "umicollapse-tag-alignment-cluster-umi-frequency-v1"
        or configuration
        != {
            "mode": "paired" if workload.paired else "single-end",
            "umi_length": workload.umi_length,
            "umi_separator_bytes": len(separator),
            "umi_separator_sha256": hashlib.sha256(separator).hexdigest(),
            "remove_unpaired": False,
            "remove_chimeric": False,
            "sort_buffer_size": "256M",
        }
        or not all(
            isinstance(receipt.get(field), bool)
            for field in boolean_fields
        )
        or not all(
            isinstance(side, dict)
            and set(side) == set(count_fields) | set(digest_fields)
            and all(
                isinstance(side.get(field), int)
                and not isinstance(side.get(field), bool)
                and int(side[field]) >= 0
                for field in count_fields
            )
            and all(
                isinstance(side.get(field), str)
                and SHA256_HEX.fullmatch(str(side[field])) is not None
                for field in digest_fields
            )
            for side in sides
        )
        or not isinstance(temporary_storage, dict)
        or set(temporary_storage)
        != {
            "persistent_stage_peak_upper_bound_bytes",
            "sort_merge_storage_note",
        }
        or not isinstance(
            temporary_storage.get(
                "persistent_stage_peak_upper_bound_bytes"
            ),
            int,
        )
        or isinstance(
            temporary_storage.get(
                "persistent_stage_peak_upper_bound_bytes"
            ),
            bool,
        )
        or int(
            temporary_storage["persistent_stage_peak_upper_bound_bytes"]
        )
        < 0
        or not isinstance(
            temporary_storage.get("sort_merge_storage_note"), str
        )
        or not str(temporary_storage["sort_merge_storage_note"]).strip()
        or ((receipt.get("equivalent") is True) != component_match)
        or return_code not in {0, 1}
        or ((return_code == 0) != (receipt.get("equivalent") is True))
    ):
        raise BenchmarkError(
            "pairwise cluster diagnostic receipt violated its contract"
        )
    upstream_metrics = directional_receipt.get("canonical_upstream")
    dumi_metrics = directional_receipt.get("dumi_off")
    if not isinstance(upstream_metrics, dict) or not isinstance(
        dumi_metrics, dict
    ):
        raise BenchmarkError(
            "directional receipt is missing pairwise cross-binding metrics"
        )
    metric_mapping = {
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
        "read_group_dictionary_sha256": (
            "read_group_dictionary_sha256"
        ),
    }
    for pairwise_side, directional_side, label in (
        (receipt["left"], upstream_metrics, "canonical upstream"),
        (receipt["right"], dumi_metrics, "dUMI off"),
    ):
        assert isinstance(pairwise_side, dict)
        if any(
            pairwise_side[pairwise_field]
            != directional_side[directional_field]
            for pairwise_field, directional_field in metric_mapping.items()
        ):
            raise BenchmarkError(
                f"pairwise {label} metrics contradict the directional receipt"
            )

    diagnostics = directional_receipt.get("diagnostics")
    if not isinstance(diagnostics, dict):
        raise BenchmarkError(
            "directional receipt is missing pairwise diagnostic booleans"
        )

    def directional_metrics_equal(
        count_field: str, digest_field: str
    ) -> bool:
        return (
            upstream_metrics.get(count_field)
            == dumi_metrics.get(count_field)
            and upstream_metrics.get(digest_field)
            == dumi_metrics.get(digest_field)
        )

    expected_components = {
        "partition_equivalent": directional_metrics_equal(
            "membership_partition_bytes",
            "membership_partition_sha256",
        ),
        "reference_dictionary_equivalent": directional_metrics_equal(
            "reference_sequences",
            "reference_dictionary_sha256",
        ),
        "read_group_dictionary_equivalent": directional_metrics_equal(
            "read_groups",
            "read_group_dictionary_sha256",
        ),
    }
    if (
        diagnostics.get(
            "canonical_upstream_dumi_off_partition_equivalent"
        )
        is not expected_components["partition_equivalent"]
        or any(
            receipt[field] is not expected
            for field, expected in expected_components.items()
        )
        or receipt["equivalent"] is not all(expected_components.values())
    ):
        raise BenchmarkError(
            "pairwise diagnostic booleans contradict the directional receipt"
        )


def run_external_directional_oracle_gate(
    *,
    workload: Workload,
    bam_input: Path,
    private_root: Path,
    directional_receipt_path: Path,
    pairwise_receipt_path: Path,
    java: Path,
    jvm_options: Sequence[str],
    classes: dict[str, Path],
    common_classpath: str,
    python: Path,
    directional_checker: Path,
    pairwise_checker: Path,
    samtools: Path,
    sort_command: Path,
) -> dict[str, dict[str, object]]:
    """Run the required source oracle and legacy pairwise diagnostic."""
    if workload.external_input is None:
        raise BenchmarkError(
            "directional-oracle gate is only defined for external workloads"
        )
    private_root.mkdir(parents=True, exist_ok=False)
    upstream_output = private_root / "canonical-upstream-tagged.private.bam"
    dumi_output = private_root / "dumi-off-tagged.private.bam"
    upstream_tmp = private_root / "canonical-upstream-java-tmp"
    dumi_tmp = private_root / "dumi-java-tmp"
    directional_tmp = private_root / "directional-checker-tmp"
    pairwise_tmp = private_root / "pairwise-checker-tmp"
    for directory in (
        upstream_tmp,
        dumi_tmp,
        directional_tmp,
        pairwise_tmp,
    ):
        directory.mkdir()

    try:
        for label, command, output in (
            (
                "canonical-upstream",
                build_java_bam_command(
                    java=java,
                    jvm_options=jvm_options,
                    java_tmp=upstream_tmp,
                    classes_root=classes["upstream"],
                    common_classpath=common_classpath,
                    bam_input=bam_input,
                    output=upstream_output,
                    workload=workload,
                    source_key="upstream",
                    streaming_mode=None,
                    tag_clusters=True,
                ),
                upstream_output,
            ),
            (
                "dumi-off",
                build_java_bam_command(
                    java=java,
                    jvm_options=jvm_options,
                    java_tmp=dumi_tmp,
                    classes_root=classes["dumi"],
                    common_classpath=common_classpath,
                    bam_input=bam_input,
                    output=dumi_output,
                    workload=workload,
                    source_key="dumi",
                    streaming_mode="off",
                    tag_clusters=True,
                ),
                dumi_output,
            ),
        ):
            completed = run_command(
                command,
                stdout_path=private_root / f"{label}-stdout.txt",
                stderr_path=private_root / f"{label}-stderr.txt",
                check=False,
                sanitize_logs=False,
            )
            if completed.returncode != 0 or not output.is_file():
                raise BenchmarkError(
                    f"untimed tagged {label} run failed for external workload "
                    f"{workload.scale!r}"
                )
            quickcheck = run_command(
                [samtools, "quickcheck", "-v", output],
                stdout_path=private_root / f"{label}-quickcheck-stdout.txt",
                stderr_path=private_root / f"{label}-quickcheck-stderr.txt",
                check=False,
                sanitize_logs=False,
            )
            if quickcheck.returncode != 0:
                raise BenchmarkError(
                    f"untimed tagged {label} output failed validation for "
                    f"external workload {workload.scale!r}"
                )

        common_checker_arguments: list[os.PathLike[str] | str] = [
            "--umi-length",
            str(workload.umi_length),
            "--umi-separator",
            workload.umi_separator,
            "--mode",
            "paired" if workload.paired else "single-end",
            "--samtools",
            samtools,
            "--sort-command",
            sort_command,
        ]
        directional_command: list[os.PathLike[str] | str] = [
            python,
            directional_checker,
            bam_input,
            upstream_output,
            dumi_output,
            "--receipt",
            directional_receipt_path,
            "--tmpdir",
            directional_tmp,
            "--edit-distance",
            "1",
            "--percentage",
            "0.5",
            *common_checker_arguments,
        ]
        directional_completed = run_command(
            directional_command,
            stdout_path=private_root / "directional-checker-stdout.txt",
            stderr_path=private_root / "directional-checker-stderr.txt",
            check=False,
            sanitize_logs=False,
        )
        if (
            directional_completed.returncode not in {0, 1}
            or not directional_receipt_path.is_file()
        ):
            raise BenchmarkError(
                f"directional-oracle checker failed for external workload "
                f"{workload.scale!r}"
            )
        try:
            directional_receipt = json.loads(
                directional_receipt_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise BenchmarkError(
                "directional-oracle checker returned an invalid receipt"
            ) from error
        if not isinstance(directional_receipt, dict):
            raise BenchmarkError(
                "directional-oracle checker returned a non-object receipt"
            )
        validate_directional_oracle_receipt(
            receipt=directional_receipt,
            return_code=directional_completed.returncode,
            workload=workload,
            source=bam_input,
            canonical_upstream=upstream_output,
            dumi_off=dumi_output,
            directional_checker=directional_checker,
            partition_checker=pairwise_checker,
        )

        pairwise_command: list[os.PathLike[str] | str] = [
            python,
            pairwise_checker,
            upstream_output,
            dumi_output,
            "--receipt",
            pairwise_receipt_path,
            "--tmpdir",
            pairwise_tmp,
            *common_checker_arguments,
        ]
        pairwise_completed = run_command(
            pairwise_command,
            stdout_path=private_root / "pairwise-checker-stdout.txt",
            stderr_path=private_root / "pairwise-checker-stderr.txt",
            check=False,
            sanitize_logs=False,
        )
        if (
            pairwise_completed.returncode not in {0, 1}
            or not pairwise_receipt_path.is_file()
        ):
            raise BenchmarkError(
                f"pairwise cluster diagnostic failed for external workload "
                f"{workload.scale!r}"
            )
        try:
            pairwise_receipt = json.loads(
                pairwise_receipt_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise BenchmarkError(
                "pairwise cluster diagnostic returned an invalid receipt"
            ) from error
        if not isinstance(pairwise_receipt, dict):
            raise BenchmarkError(
                "pairwise cluster diagnostic returned a non-object receipt"
            )
        validate_pairwise_cluster_diagnostic_receipt(
            receipt=pairwise_receipt,
            return_code=pairwise_completed.returncode,
            workload=workload,
            directional_receipt=directional_receipt,
        )
        return {
            "directional": directional_receipt,
            "pairwise": pairwise_receipt,
        }
    finally:
        try:
            suppress_external_log_contents(private_root)
        finally:
            try:
                shutil.rmtree(private_root)
            except OSError as error:
                raise BenchmarkError(
                    f"could not remove private directional-oracle artifacts "
                    f"for external workload {workload.scale!r}"
                ) from error


def annotate_external_directional_oracle_measurements(
    *,
    measurement_path: Path,
    results: dict[str, tuple[dict[str, object], str]],
) -> None:
    """Bind every external row to its post-timing directional-oracle gate."""
    try:
        with measurement_path.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream, delimiter="\t")
            if reader.fieldnames != MEASUREMENT_COLUMNS:
                raise BenchmarkError(
                    "measurement columns changed before directional annotation"
                )
            rows = list(reader)
    except OSError as error:
        raise BenchmarkError(
            "could not read measurements for directional annotation"
        ) from error

    observed_scales = {
        row["scale"] for row in rows if row["workload"] == "external"
    }
    if observed_scales != set(results):
        raise BenchmarkError(
            "directional-oracle receipts do not cover every external workload"
        )
    for row in rows:
        if row["workload"] != "external":
            continue
        receipt, receipt_record = results[row["scale"]]
        gate = receipt.get("gate")
        diagnostics = receipt.get("diagnostics")
        if (
            not isinstance(gate, dict)
            or not isinstance(diagnostics, dict)
            or gate.get("directional_oracle_gate_pass") is not True
        ):
            raise BenchmarkError(
                f"directional-oracle gate failed for external workload "
                f"{row['scale']!r}"
            )
        for field in (
            "directional_oracle_gate_pass",
            "dumi_off_oracle_partition_equivalent",
            "dumi_off_oracle_root_assignment_equivalent",
        ):
            row[field] = str(gate[field])
        for field in (
            "canonical_upstream_oracle_partition_equivalent",
            "canonical_upstream_oracle_root_assignment_equivalent",
            "canonical_upstream_dumi_off_partition_equivalent",
            "canonical_upstream_dumi_off_root_assignment_equivalent",
        ):
            row[field] = str(diagnostics[field])
        row["directional_oracle_receipt"] = receipt_record

    temporary = measurement_path.with_name(measurement_path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=MEASUREMENT_COLUMNS,
                delimiter="\t",
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(measurement_path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise BenchmarkError(
            "could not publish directional-oracle measurement annotations"
        ) from error


def probe_forced_streaming_contract(
    *,
    workload: Workload,
    bam_input: Path,
    root: Path,
    java: Path,
    jvm_options: Sequence[str],
    classes_root: Path,
    common_classpath: str,
    samtools: Path,
) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=True)
    output = root / "forced-on.private.bam"
    java_tmp = root / "java-tmp"
    java_tmp.mkdir()
    command = build_java_bam_command(
        java=java,
        jvm_options=jvm_options,
        java_tmp=java_tmp,
        classes_root=classes_root,
        common_classpath=common_classpath,
        bam_input=bam_input,
        output=output,
        workload=workload,
        source_key="dumi",
        streaming_mode="on",
    )
    (root / "forced-on-command.txt").write_text(
        command_text(command) + "\n", encoding="utf-8"
    )
    stdout_path = root / "forced-on-stdout.txt"
    stderr_path = root / "forced-on-stderr.txt"
    try:
        completed = run_command(
            command,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            check=False,
            sanitize_logs=False,
        )
        stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
        stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
        marker_seen = STREAMING_MARKER in stdout_text
        fallback_seen = STREAMING_FALLBACK_MARKER in stderr_text
        if completed.returncode == 0:
            if not output.is_file():
                raise BenchmarkError(
                    f"forced-on contract produced no output for "
                    f"workload {workload.scale!r}"
                )
            run_command([samtools, "quickcheck", "-v", output])
            sort_order, _, _ = external_header_summary(
                samtools=samtools, bam_path=output
            )
            route = observed_execution_route(
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                sort_order=sort_order,
                implementation_name="dumi",
                requested_mode="on",
                paired=workload.paired,
                context=f"forced-on contract for external/{workload.scale}",
            )
            eligible = True
            rejection_reason = None
        else:
            rejection_reason: str | None = None
            if (
                workload.paired
                and completed.returncode == 2
                and PAIRED_STREAMING_REJECTION in stderr_text
                and not marker_seen
                and not fallback_seen
            ):
                rejection_reason = "paired-mode-incompatible"
            elif (
                not workload.paired
                and completed.returncode == 1
                and marker_seen
                and not fallback_seen
            ):
                rejection_reason = next(
                    (
                        reason
                        for reason, diagnostic in (
                            STREAMING_DATA_REJECTION_MARKERS.items()
                        )
                        if diagnostic in stderr_text
                    ),
                    None,
                )
            if output.exists() or rejection_reason is None:
                raise BenchmarkError(
                    f"forced-on contract failed unexpectedly for "
                    f"workload {workload.scale!r}"
                )
            sort_order = None
            route = "rejected-ineligible"
            eligible = False
        receipt = {
            "status": "pass",
            "eligible": eligible,
            "timed_cell_scheduled": eligible,
            "observed_route": route,
            "exit_code": completed.returncode,
            "output_created": output.exists(),
            "streaming_marker_seen": marker_seen,
            "fallback_marker_seen": fallback_seen,
            "observed_sort_order": sort_order,
            "rejection_reason": rejection_reason,
            "logs_suppressed": True,
        }
        (root / "forced-on-receipt.json").write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return receipt
    finally:
        suppress_external_log_contents(root)
        for candidate in {
            output,
            Path(os.fspath(output) + ".bai"),
            Path(os.fspath(output) + ".csi"),
        }:
            candidate.unlink(missing_ok=True)


def timed_command(
    *,
    command: Sequence[os.PathLike[str] | str],
    run_root: Path,
    gnu_time: Path,
) -> tuple[int, tuple[str, str, str, str, str, str]]:
    run_root.mkdir(parents=True, exist_ok=True)
    command_path = run_root / "command.txt"
    stdout_path = run_root / "stdout.txt"
    stderr_path = run_root / "stderr.txt"
    metrics_path = run_root / "time.tsv"
    command_path.write_text(command_text(command) + "\n", encoding="utf-8")
    timed = [
        gnu_time,
        "-f",
        "%e\t%U\t%S\t%P\t%M\t%x",
        "-o",
        metrics_path,
        *command,
    ]
    started_ns = time.perf_counter_ns()
    completed = run_command(
        timed,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        check=False,
        sanitize_logs=False,
    )
    elapsed_s = (time.perf_counter_ns() - started_ns) / 1_000_000_000
    wall_path = run_root / "monotonic-wall-seconds.txt"
    wall_path.write_text(f"{elapsed_s:.9f}\n", encoding="utf-8")
    metrics = list(parse_time_metrics(metrics_path))
    metrics[0] = f"{elapsed_s:.9f}"
    return completed.returncode, tuple(metrics)  # type: ignore[return-value]


def time_benchmark_cell(
    *,
    stage: str,
    cell: ScheduledCell,
    workload: Workload,
    output_root: Path,
    java: Path,
    jvm_options: Sequence[str],
    classes: dict[str, Path],
    common_classpath: str,
    bam_input: Path,
    input_hash: str,
    input_index_hash: str,
    samtools: Path,
    gnu_time: Path,
    expected_raw_sort_order: str,
    expected_route: str,
    exact_reference: Path | None,
    exact_reference_canonical: Path | None,
    exact_reference_canonical_receipt: Path | None,
    exact_expectation: dict[str, object],
    oracle_implementation: str,
) -> PendingTimedCell:
    """Execute one timed cell without post-run semantic validation."""
    implementation = cell.implementation
    run_id = (
        f"{workload.name}-{workload.scale}-"
        f"r{cell.repetition:02d}-o{cell.order:02d}-"
        f"{implementation.label}"
    )
    stage_root = output_root / "runs" / run_id / stage
    if stage == "raw":
        java_output = stage_root / "output.bam"
        measured_output = java_output
    elif stage == "end_to_end_ready":
        measured_output = stage_root / "output.coordinate.bam"
        java_output = (
            stage_root / "intermediate.raw.private.bam"
            if expected_raw_sort_order == "unsorted"
            else measured_output
        )
    else:
        raise BenchmarkError(f"unknown measured stage {stage!r}")
    java_tmp = stage_root / "java-tmp"
    java_tmp.mkdir(parents=True, exist_ok=True)
    java_command = build_java_bam_command(
        java=java,
        jvm_options=jvm_options,
        java_tmp=java_tmp,
        classes_root=classes[implementation.source_key],
        common_classpath=common_classpath,
        bam_input=bam_input,
        output=java_output,
        workload=workload,
        source_key=implementation.source_key,
        streaming_mode=(
            implementation.mode
            if implementation.name == "dumi"
            else None
        ),
    )
    timed_stage_command = (
        java_command
        if stage == "raw"
        else build_end_to_end_ready_command(
            java_command=java_command,
            raw_sort_order=expected_raw_sort_order,
            java_output=java_output,
            final_output=measured_output,
            samtools=samtools,
        )
    )
    preread_benchmark_input(
        samtools=samtools,
        bam_input=bam_input,
        expected_bam_sha256=input_hash,
        expected_index_sha256=input_index_hash,
        root=stage_root,
    )
    exit_code, metrics = timed_command(
        command=timed_stage_command,
        run_root=stage_root,
        gnu_time=gnu_time,
    )
    if exit_code != 0:
        sanitize_text_file(stage_root / "stdout.txt")
        sanitize_text_file(stage_root / "stderr.txt")
        raise BenchmarkError(
            f"timed command failed; evidence retained in {stage_root}"
        )
    actual_route = observed_execution_route(
        stdout_path=stage_root / "stdout.txt",
        stderr_path=stage_root / "stderr.txt",
        sort_order=expected_raw_sort_order,
        implementation_name=implementation.name,
        requested_mode=implementation.mode,
        paired=workload.paired,
        context=run_id,
    )
    validate_external_route_contract(
        workload=workload,
        implementation_name=implementation.name,
        requested_mode=implementation.mode,
        observed_route=actual_route,
        context=run_id,
    )
    if actual_route != expected_route:
        raise BenchmarkError(
            f"timed route changed from the validated warm-up route in {run_id}"
        )
    sanitize_text_file(stage_root / "stdout.txt")
    sanitize_text_file(stage_root / "stderr.txt")
    return PendingTimedCell(
        stage=stage,
        cell=cell,
        run_id=run_id,
        stage_root=stage_root,
        java_output=java_output,
        measured_output=measured_output,
        expected_raw_sort_order=expected_raw_sort_order,
        actual_route=actual_route,
        exit_code=exit_code,
        metrics=metrics,
        exact_reference=exact_reference,
        exact_reference_canonical=exact_reference_canonical,
        exact_reference_canonical_receipt=(
            exact_reference_canonical_receipt
        ),
        exact_expectation=exact_expectation,
        oracle_implementation=oracle_implementation,
    )


def validate_benchmark_cell(
    *,
    pending: PendingTimedCell,
    workload: Workload,
    output_root: Path,
    checker: Path,
    python: Path,
    samtools: Path,
    input_hash: str,
    cross_implementation_receipt: dict[str, object],
    keep_outputs: bool,
) -> dict[str, object]:
    """Validate, record, and clean one cell after its timing block completes."""
    implementation = pending.cell.implementation
    if pending.stage == "end_to_end_ready":
        indexed = run_command(
            [samtools, "idxstats", pending.measured_output],
            stdout_path=pending.stage_root / "index-validation-stdout.txt",
            stderr_path=pending.stage_root / "index-validation-stderr.txt",
            check=False,
        )
        if indexed.returncode != 0:
            raise BenchmarkError(
                f"end-to-end-ready output index is unusable in "
                f"{pending.run_id}"
            )
        adjacent_bam_index(pending.measured_output)

    output_inspection = inspect_output(
        checker=checker,
        python=python,
        samtools=samtools,
        output=pending.measured_output,
        temporary_root=pending.stage_root / "semantic-tmp",
        reference=pending.exact_reference,
        reference_canonical=pending.exact_reference_canonical,
        reference_canonical_receipt=(
            pending.exact_reference_canonical_receipt
        ),
        reference_canonical_sha256=(
            str(pending.exact_expectation["semantic_sha256"])
            if pending.exact_reference_canonical is not None
            else None
        ),
        alignment_group_mode=(
            "paired" if workload.paired else "single-end"
        ),
    )
    expected_output_sort_order = (
        pending.expected_raw_sort_order
        if pending.stage == "raw"
        else "coordinate"
    )
    if output_inspection["sort_order"] != expected_output_sort_order:
        raise BenchmarkError(
            f"{pending.stage} output has unexpected sort order in "
            f"{pending.run_id}"
        )
    output_inspection["actual_route"] = pending.actual_route
    if workload.external_input is not None:
        (pending.stage_root / "inspection.json").write_text(
            json.dumps(output_inspection, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if (
        int(output_inspection["output_records"])
        != int(pending.exact_expectation["output_records"])
        or str(output_inspection["semantic_sha256"])
        != str(pending.exact_expectation["semantic_sha256"])
        or int(output_inspection["reference_sequences"])
        != int(pending.exact_expectation["reference_sequences"])
        or str(output_inspection["reference_dictionary_sha256"])
        != str(
            pending.exact_expectation["reference_dictionary_sha256"]
        )
        or (
            pending.exact_reference is not None
            and output_inspection["exact_oracle_match"] is not True
        )
    ):
        raise BenchmarkError(
            f"output does not match the workload oracle in {pending.run_id}"
        )
    elapsed, user_s, system_s, cpu_pct, rss, timed_exit = pending.metrics
    row: dict[str, object] = {
        "run_id": f"{pending.run_id}-{pending.stage}",
        "workload": workload.name,
        "scale": workload.scale,
        "stage": pending.stage,
        "implementation": implementation.name,
        "mode": implementation.mode,
        "repetition": pending.cell.repetition,
        "order": pending.cell.order,
        "exit_code": timed_exit or pending.exit_code,
        "elapsed_s": elapsed,
        "user_s": user_s,
        "system_s": system_s,
        "cpu_pct": cpu_pct,
        "max_rss_kib": rss,
        "input_sha256": input_hash,
        "output_records": output_inspection["output_records"],
        "semantic_sha256": output_inspection["semantic_sha256"],
        "sort_order": output_inspection["sort_order"],
        "output_bytes": output_inspection["output_bytes"],
        "output_sha256": output_inspection["output_sha256"],
        "reference_sequences": output_inspection["reference_sequences"],
        "reference_dictionary_sha256": output_inspection[
            "reference_dictionary_sha256"
        ],
        "expected_output_records": pending.exact_expectation[
            "output_records"
        ],
        "expected_semantic_sha256": pending.exact_expectation[
            "semantic_sha256"
        ],
        "expected_reference_sequences": pending.exact_expectation[
            "reference_sequences"
        ],
        "expected_reference_dictionary_sha256": pending.exact_expectation[
            "reference_dictionary_sha256"
        ],
        "actual_route": pending.actual_route,
        "oracle_implementation": pending.oracle_implementation,
        "exact_oracle_match": output_inspection["exact_oracle_match"],
        "cross_implementation_exact_match": (
            cross_implementation_receipt["exact_match"]
            if workload.external_input is not None
            else True
        ),
        "cross_implementation_output_count_match": (
            cross_implementation_receipt["output_count_match"]
            if workload.external_input is not None
            else True
        ),
        "cross_implementation_alignment_group_output_count_match": (
            cross_implementation_receipt[
                "alignment_group_output_count_match"
            ]
            if workload.external_input is not None
            else True
        ),
        "command_file": record_path(
            pending.stage_root / "command.txt", output_root
        ),
        "stdout_file": record_path(
            pending.stage_root / "stdout.txt", output_root
        ),
        "stderr_file": record_path(
            pending.stage_root / "stderr.txt", output_root
        ),
        "output_file": (
            record_path(pending.measured_output, output_root)
            if keep_outputs
            else ""
        ),
    }
    if pending.stage == "end_to_end_ready":
        pending.java_output.unlink(missing_ok=True)
    if not keep_outputs:
        for candidate in {
            pending.java_output,
            pending.measured_output,
            Path(str(pending.java_output) + ".bai"),
            Path(str(pending.java_output) + ".csi"),
            Path(str(pending.measured_output) + ".bai"),
            Path(str(pending.measured_output) + ".csi"),
        }:
            candidate.unlink(missing_ok=True)
    return row


def find_java(args: argparse.Namespace, repository_root: Path) -> tuple[Path, Path]:
    homes: list[Path] = []
    if args.java_home:
        homes.append(Path(args.java_home))
    if os.environ.get("JAVA_HOME"):
        homes.append(Path(os.environ["JAVA_HOME"]))
    homes.append(repository_root / ".tools" / "jdk")
    for home in homes:
        java = home / "bin" / "java"
        javac = home / "bin" / "javac"
        if java.is_file() and javac.is_file():
            return java.resolve(), javac.resolve()
    return require_tool("java"), require_tool("javac")


def find_gnu_time(explicit: str | None) -> Path:
    candidates = [explicit] if explicit else ["gtime", "/usr/bin/time"]
    for candidate in candidates:
        if not candidate:
            continue
        resolved = shutil.which(candidate) if "/" not in candidate else candidate
        if not resolved:
            continue
        path = Path(resolved).resolve()
        completed = run_command([path, "--version"], check=False)
        version = (completed.stdout or "") + (completed.stderr or "")
        if completed.returncode == 0 and "GNU" in version:
            return path
    raise BenchmarkError("GNU time is required ('time' on Linux or Homebrew 'gtime')")


def find_gnu_sort(explicit: str | None) -> Path:
    """Resolve a GNU coreutils sort, preferring Homebrew's gsort."""
    candidates = [explicit] if explicit else ["gsort", "sort"]
    for candidate in candidates:
        if not candidate:
            continue
        resolved = shutil.which(candidate)
        if not resolved:
            continue
        path = Path(resolved).resolve()
        completed = run_command([path, "--version"], check=False)
        version = (completed.stdout or "") + (completed.stderr or "")
        if completed.returncode == 0 and "GNU coreutils" in version:
            return path
    raise BenchmarkError(
        "GNU sort is required for external mode "
        "('sort' on Linux or Homebrew 'gsort'); pass --sort-command explicitly"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run source-normalized canonical UMICollapse versus dUMI benchmarks."
    )
    parser.add_argument("--output-dir", help="new/empty evidence directory (default: /tmp)")
    parser.add_argument("--dumi-ref", default="HEAD", help="committed dUMI Git ref to benchmark")
    parser.add_argument("--repetitions", type=int, default=8)
    parser.add_argument(
        "--external-bam-manifest",
        "--external-inputs",
        dest="external_bam_manifest",
        help=(
            "TSV or JSON manifest of complete pre-deduplication, "
            "coordinate-sorted BAMs; uses separate untimed dUMI-off and "
            "canonical-upstream exact oracles"
        ),
    )
    parser.add_argument(
        "--external-provenance-ledger",
        help=(
            "private JSON authorization/provenance ledger covering every "
            "external workload"
        ),
    )
    parser.add_argument(
        "--external-provenance-ledger-sha256",
        help="required SHA-256 of --external-provenance-ledger",
    )
    parser.add_argument(
        "--workloads",
        default=None,
        help="comma-separated subset: sparse,moderate,hotspot,paired",
    )
    parser.add_argument("--profile", choices=("standard", "tiny"), default="standard")
    parser.add_argument("--sparse-records", default="100000,1000000")
    parser.add_argument("--moderate-groups", type=int, default=4096)
    parser.add_argument("--moderate-families-per-group", type=int, default=16)
    parser.add_argument("--hotspot-families", type=int, default=65536)
    parser.add_argument("--paired-references", default="10,1000")
    parser.add_argument("--paired-pairs-per-reference", type=int, default=5)
    parser.add_argument("--include-intermediate", action="store_true")
    parser.add_argument("--keep-outputs", action="store_true")
    parser.add_argument("--allow-output-in-repo", action="store_true")
    parser.add_argument("--java-home")
    parser.add_argument("--samtools")
    parser.add_argument("--gnu-time")
    parser.add_argument(
        "--sort-command",
        help=(
            "GNU coreutils sort for the external directional-oracle gate "
            "(default: auto-detect gsort, then sort)"
        ),
    )
    parser.add_argument("--active-processors", type=int, default=8)
    parser.add_argument("--xms", default="64m")
    parser.add_argument("--xmx", default="4g")
    parser.add_argument(
        "--cluster-tag-xmx",
        default=None,
        help=(
            "maximum heap for untimed external --tag correctness gates "
            "(default: inherit --xmx; size explicitly for representative inputs)"
        ),
    )
    parser.add_argument("--seed", type=int, default=1729)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    global ACTIVE_OUTPUT_ROOT, ACTIVE_EXTERNAL_INPUT_MODE, PUBLIC_PATH_REPLACEMENTS
    os.umask(0o077)
    ACTIVE_OUTPUT_ROOT = None
    ACTIVE_EXTERNAL_INPUT_MODE = False
    PUBLIC_PATH_REPLACEMENTS = []
    args = build_parser().parse_args(argv)
    original_home = Path.home()
    removed_environment_variables = sorted(
        key for key in INJECTION_ENVIRONMENT_VARIABLES if key in os.environ
    )
    for key in INJECTION_ENVIRONMENT_VARIABLES:
        os.environ.pop(key, None)
    os.environ.update(BENCHMARK_ENVIRONMENT)
    if args.repetitions <= 0:
        raise BenchmarkError("--repetitions must be positive")
    if args.active_processors <= 0:
        raise BenchmarkError("--active-processors must be positive")
    if args.external_bam_manifest:
        if (
            not args.external_provenance_ledger
            or not args.external_provenance_ledger_sha256
        ):
            raise BenchmarkError(
                "--external-provenance-ledger and "
                "--external-provenance-ledger-sha256 are required with "
                "--external-bam-manifest"
            )
        if args.keep_outputs:
            raise BenchmarkError(
                "--keep-outputs is not permitted with external BAM inputs"
            )
        if args.allow_output_in_repo:
            raise BenchmarkError(
                "--allow-output-in-repo is not permitted with external BAM inputs"
            )
        if args.workloads is not None:
            raise BenchmarkError(
                "--workloads cannot be combined with --external-bam-manifest"
            )
        if args.profile != "standard":
            raise BenchmarkError(
                "--profile cannot be combined with --external-bam-manifest"
            )
        if args.include_intermediate:
            raise BenchmarkError(
                "--include-intermediate cannot be combined with "
                "--external-bam-manifest"
            )
    else:
        if (
            args.external_provenance_ledger is not None
            or args.external_provenance_ledger_sha256 is not None
        ):
            raise BenchmarkError(
                "external provenance-ledger options require "
                "--external-bam-manifest"
            )
        if args.sort_command is not None:
            raise BenchmarkError(
                "--sort-command is only used with --external-bam-manifest"
            )
        if args.moderate_groups <= 0 or args.hotspot_families <= 0:
            raise BenchmarkError("workload sizes must be positive")
        if (
            args.paired_pairs_per_reference <= 0
            or args.moderate_families_per_group <= 0
        ):
            raise BenchmarkError("workload multiplicities must be positive")

    script_root = Path(__file__).resolve().parent
    repository_root = script_root.parents[1]
    python = Path(sys.executable).resolve()
    generator = script_root / "generate_workload.py"
    checker = script_root / "semantic_check.py"
    cluster_partition_checker = script_root / "cluster_partition_check.py"
    directional_oracle_checker = (
        script_root / "directional_oracle_check.py"
    )
    summarizer = script_root / "summarize_results.py"
    benchmark_readme = script_root / "README.md"
    harness_sources = (
        Path(__file__).resolve(),
        generator,
        checker,
        cluster_partition_checker,
        directional_oracle_checker,
        summarizer,
        benchmark_readme,
    )
    for helper in harness_sources:
        if not helper.is_file():
            raise BenchmarkError(f"benchmark helper is missing: {helper}")

    output_root = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else Path(os.environ.get("TMPDIR", "/tmp")) / f"dumi-benchmark-{utc_stamp()}"
    )
    if is_within(output_root, repository_root):
        if args.external_bam_manifest:
            raise BenchmarkError(
                "external-input evidence must be written outside the repository"
            )
        if not args.allow_output_in_repo:
            raise BenchmarkError(
                "refusing to place generated benchmark data inside the repository; "
                "choose an external --output-dir or pass --allow-output-in-repo"
            )
    PUBLIC_PATH_REPLACEMENTS = [
        (os.fspath(output_root), "<EVIDENCE_DIR>"),
        (os.fspath(repository_root), "<DUMI_REPOSITORY>"),
        (os.fspath(original_home), "<HOME>"),
    ]
    external_entries = (
        parse_external_manifest(args.external_bam_manifest)
        if args.external_bam_manifest
        else []
    )
    external_provenance_ledger_path: Path | None = None
    external_provenance_ledger_receipt: dict[str, object] | None = None
    if external_entries:
        assert args.external_provenance_ledger is not None
        assert args.external_provenance_ledger_sha256 is not None
        (
            external_provenance_ledger_path,
            external_provenance_ledger_receipt,
        ) = validate_external_provenance_ledger(
            path_string=args.external_provenance_ledger,
            expected_sha256=args.external_provenance_ledger_sha256,
            external_entries=external_entries,
        )
    ensure_empty_output(output_root)
    ACTIVE_OUTPUT_ROOT = output_root
    ACTIVE_EXTERNAL_INPUT_MODE = bool(args.external_bam_manifest)
    process_tmp = output_root / "process-tmp"
    process_tmp.mkdir()
    process_home = output_root / "process-home"
    process_home.mkdir()
    os.environ["TMPDIR"] = os.fspath(process_tmp)
    write_status(output_root, "RUNNING")

    harness_snapshot_root = output_root / "harness"
    harness_snapshot_root.mkdir()
    harness_files: list[dict[str, str]] = []
    for source in harness_sources:
        destination = harness_snapshot_root / source.name
        shutil.copy2(source, destination)
        harness_files.append(
            {
                "path": record_path(destination, output_root),
                "sha256": sha256_file(destination),
            }
        )
    generator = harness_snapshot_root / generator.name
    checker = harness_snapshot_root / checker.name
    cluster_partition_checker = (
        harness_snapshot_root / cluster_partition_checker.name
    )
    directional_oracle_checker = (
        harness_snapshot_root / directional_oracle_checker.name
    )
    summarizer = harness_snapshot_root / summarizer.name

    git = require_tool("git")
    curl = require_tool("curl")
    samtools = require_tool("samtools", args.samtools)
    gnu_time = find_gnu_time(args.gnu_time)
    gnu_sort = (
        find_gnu_sort(args.sort_command)
        if args.external_bam_manifest
        else None
    )
    java, javac = find_java(args, repository_root)
    if java.parent != javac.parent:
        raise BenchmarkError(
            "java and javac must resolve to the same JDK bin directory"
        )
    PUBLIC_PATH_REPLACEMENTS.extend(
        [
            (os.fspath(python), "<PYTHON>"),
            (os.fspath(git), "<GIT>"),
            (os.fspath(curl), "<CURL>"),
            (os.fspath(samtools), "<SAMTOOLS>"),
            (os.fspath(gnu_time), "<GNU_TIME>"),
            *(
                [(os.fspath(gnu_sort), "<GNU_SORT>")]
                if gnu_sort is not None
                else []
            ),
            (os.fspath(java), "<JAVA>"),
            (os.fspath(javac), "<JAVAC>"),
        ]
    )
    pass_through_names = (
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
    )
    pass_through_environment = {
        key: os.environ[key] for key in pass_through_names if key in os.environ
    }
    path_directories = list(
        dict.fromkeys(
            [
                os.fspath(path.parent)
                for path in (
                    python,
                    git,
                    curl,
                    samtools,
                    gnu_time,
                    *([gnu_sort] if gnu_sort is not None else []),
                    java,
                    javac,
                )
            ]
            + ["/usr/local/bin", "/usr/bin", "/bin", "/opt/homebrew/bin"]
        )
    )
    os.environ.clear()
    os.environ.update(pass_through_environment)
    os.environ.update(BENCHMARK_ENVIRONMENT)
    os.environ.update(
        {
            "HOME": os.fspath(process_home),
            "PATH": os.pathsep.join(path_directories),
            "TMPDIR": os.fspath(process_tmp),
        }
    )

    if external_entries:
        selected: list[str] = []
        sparse_records: list[int] = []
        paired_references: list[int] = []
        if external_provenance_ledger_receipt is None:
            raise BenchmarkError(
                "external provenance ledger receipt is missing"
            )
        external_validation_receipts = {}
        for entry in external_entries:
            validation_root = (
                output_root
                / "input-validation"
                / "external"
                / entry.workload_id
            )
            receipt = validate_external_bam(
                entry=entry,
                samtools=samtools,
                validation_root=validation_root,
            )
            receipt["provenance_ledger"] = dict(
                external_provenance_ledger_receipt
            )
            (validation_root / "receipt.json").write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            external_validation_receipts[entry.workload_id] = receipt
    else:
        selected_value = args.workloads or "sparse,moderate,hotspot,paired"
        selected = [
            item.strip() for item in selected_value.split(",") if item.strip()
        ]
        allowed = {"sparse", "moderate", "hotspot", "paired"}
        if not selected or set(selected) - allowed:
            raise BenchmarkError(f"--workloads must be a subset of {sorted(allowed)}")
        if len(selected) != len(set(selected)):
            raise BenchmarkError("--workloads must not contain duplicate values")

        sparse_records = parse_positive_list(args.sparse_records, "--sparse-records")
        paired_references = parse_positive_list(
            args.paired_references, "--paired-references"
        )
        if args.profile == "tiny":
            sparse_records = [1000]
            args.moderate_groups = 20
            args.moderate_families_per_group = 1
            args.hotspot_families = 32
            paired_references = [3]
            args.paired_pairs_per_reference = 2
        if args.moderate_groups * args.moderate_families_per_group > 65_536:
            raise BenchmarkError(
                "moderate groups multiplied by families per group cannot exceed 65,536"
            )
        if args.hotspot_families > 65_536:
            raise BenchmarkError("--hotspot-families cannot exceed 65,536")
        external_validation_receipts = {}

    if external_entries:
        resolved_ref = subprocess.run(
            [
                os.fspath(git),
                "-C",
                os.fspath(repository_root),
                "rev-parse",
                f"{args.dumi_ref}^{{commit}}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=os.environ.copy(),
            check=False,
        )
        if resolved_ref.returncode != 0:
            raise BenchmarkError("could not resolve the requested dUMI ref")
        dumi_sha = (resolved_ref.stdout or "").strip()
    else:
        dumi_sha = capture(
            [git, "-C", repository_root, "rev-parse", f"{args.dumi_ref}^{{commit}}"]
        )
    if not git_has_commit(git, repository_root, dumi_sha):
        if external_entries:
            raise BenchmarkError("the resolved dUMI ref is not a commit")
        raise BenchmarkError(f"dUMI ref is not a commit: {args.dumi_ref}")
    worktree_status = capture(
        [git, "-C", repository_root, "status", "--porcelain", "--untracked-files=normal"]
    )
    if worktree_status:
        print(
            "warning: the worktree is dirty; timed production sources will come only "
            f"from archived commit {dumi_sha}",
            file=sys.stderr,
        )

    source_cache = output_root / "source-cache"
    source_cache.mkdir()
    canonical_repository = source_repository(
        git=git,
        local_repository=repository_root,
        cache_root=source_cache,
        cache_name="canonical",
        url=CANONICAL_URL,
        ref=CANONICAL_REF,
        sha=CANONICAL_SHA,
    )
    intermediate_repository: Path | None = None
    has_paired_workload = (
        any(entry.paired for entry in external_entries)
        if external_entries
        else "paired" in selected
    )
    if args.include_intermediate and has_paired_workload:
        intermediate_repository = source_repository(
            git=git,
            local_repository=repository_root,
            cache_root=source_cache,
            cache_name="intermediate",
            url=INTERMEDIATE_URL,
            ref=INTERMEDIATE_REF,
            sha=INTERMEDIATE_SHA,
        )

    source_root = output_root / "sources"
    sources = {
        "upstream": (canonical_repository, CANONICAL_SHA),
        "dumi": (repository_root, dumi_sha),
    }
    if intermediate_repository:
        sources["intermediate"] = (intermediate_repository, INTERMEDIATE_SHA)
    for label, (repository, sha) in sources.items():
        archive_commit(git, repository, sha, source_root / label)
    harness_commit_binding = (
        verify_external_harness_commit_binding(
            git=git,
            repository_root=repository_root,
            dumi_sha=dumi_sha,
            harness_sources=harness_sources,
            harness_snapshot_root=harness_snapshot_root,
            archived_dumi_root=source_root / "dumi",
            output_root=output_root,
        )
        if external_entries
        else None
    )

    lock_path = source_root / "dumi" / "dependencies.lock"
    dependencies = parse_dependency_lock(lock_path)
    dependency_paths = prepare_dependencies(
        dependencies=dependencies,
        destination=output_root / "dependencies",
        repository_root=repository_root,
        curl=curl,
    )
    common_classpath = os.pathsep.join(os.fspath(path) for path in dependency_paths)

    builds: dict[str, dict[str, object]] = {}
    classes: dict[str, Path] = {}
    for label in sources:
        classes[label] = output_root / "classes" / label
        builds[label] = compile_source(
            label=label,
            source_root=source_root / label,
            classes_root=classes[label],
            dependency_paths=dependency_paths,
            javac=javac,
            command_root=output_root / "build-commands",
        )

    jvm_options = [
        "-XX:-UsePerfData",
        "-server",
        f"-Xms{args.xms}",
        f"-Xmx{args.xmx}",
        "-Xss20m",
        f"-XX:ActiveProcessorCount={args.active_processors}",
    ]
    effective_cluster_tag_xmx = args.cluster_tag_xmx or args.xmx
    cluster_tag_jvm_options = [
        option for option in jvm_options if not option.startswith("-Xmx")
    ] + [f"-Xmx{effective_cluster_tag_xmx}"]
    external_runtime_inputs: dict[str, Path] = {}
    external_streaming_receipts: dict[str, dict[str, object]] = {}
    if external_entries:
        for entry in external_entries:
            private_input, snapshot_receipt = snapshot_external_input(
                entry=entry,
                validation_receipt=external_validation_receipts[entry.workload_id],
                destination_root=(
                    output_root / "private-inputs" / entry.workload_id
                ),
            )
            private_index_candidates = [
                Path(os.fspath(private_input) + suffix)
                for suffix in (".bai", ".csi")
            ]
            if not any(path.is_file() for path in private_index_candidates):
                index_root = (
                    output_root
                    / "private-inputs"
                    / entry.workload_id
                    / "index-build"
                )
                index_root.mkdir()
                run_command(
                    [samtools, "index", private_input],
                    stdout_path=index_root / "stdout.txt",
                    stderr_path=index_root / "stderr.txt",
                )
            private_index = adjacent_bam_index(private_input)
            os.chmod(private_index, 0o400)
            snapshot_receipt["timing_index"] = {
                "bytes": private_index.stat().st_size,
                "sha256": sha256_file(private_index),
                "format": private_index.suffix.removeprefix("."),
                "path_recorded": False,
            }
            external_runtime_inputs[entry.workload_id] = private_input
            probe_workload = Workload(
                "external",
                entry.workload_id,
                entry.umi_length,
                entry.paired,
                (),
                umi_separator=entry.umi_separator,
                external_input=entry,
            )
            forced_on_receipt = probe_forced_streaming_contract(
                workload=probe_workload,
                bam_input=private_input,
                root=(
                    output_root
                    / "contracts"
                    / "external"
                    / entry.workload_id
                    / "forced-on-eligibility"
                ),
                java=java,
                jvm_options=jvm_options,
                classes_root=classes["dumi"],
                common_classpath=common_classpath,
                samtools=samtools,
            )
            external_streaming_receipts[entry.workload_id] = forced_on_receipt
            external_validation_receipts[entry.workload_id][
                "private_timing_snapshot"
            ] = snapshot_receipt
            external_validation_receipts[entry.workload_id][
                "forced_on_contract"
            ] = forced_on_receipt

    environment_commands = [
        ("uname", ["uname", "-srmo"]),
        ("java", [java, "-version"]),
        ("javac", [javac, "-version"]),
        ("samtools", [samtools, "--version"]),
        ("gnu_time", [gnu_time, "--version"]),
        ("git", [git, "--version"]),
        ("python", [python, "--version"]),
    ]
    if gnu_sort is not None:
        environment_commands.append(("gnu_sort", [gnu_sort, "--version"]))
    if shutil.which("lscpu"):
        environment_commands.append(("lscpu", ["lscpu"]))
    environment_sections: list[str] = []
    environment_json: dict[str, object] = {
        "captured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "platform": sanitize_public_text(platform.platform()),
        "python": sanitize_public_text(sys.version),
        "logical_cpu_count": os.cpu_count(),
        "load_average_1m_5m_15m": (
            list(os.getloadavg()) if hasattr(os, "getloadavg") else None
        ),
        "removed_injection_environment_variables": removed_environment_variables,
        "environment_policy": "allowlist",
        "network_environment_variable_names": sorted(pass_through_environment),
        "subprocess_environment": {
            **BENCHMARK_ENVIRONMENT,
            "HOME": "<EVIDENCE_DIR>/process-home",
            "PATH": sanitize_public_text(os.environ["PATH"]),
            "TMPDIR": "<EVIDENCE_DIR>/process-tmp",
        },
    }
    if hasattr(os, "sched_getaffinity"):
        environment_json["cpu_affinity"] = sorted(os.sched_getaffinity(0))
    governor_path = Path(
        "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"
    )
    if governor_path.is_file():
        environment_json["cpu_scaling_governor"] = governor_path.read_text(
            encoding="utf-8", errors="replace"
        ).strip()
    for label, command in environment_commands:
        value = sanitize_public_text(capture(command))
        environment_json[label] = value
        environment_sections.extend([f"## {label}", command_text(command), value, ""])
    (output_root / "environment.json").write_text(
        json.dumps(environment_json, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_root / "environment.txt").write_text(
        "\n".join(environment_sections), encoding="utf-8"
    )

    manifest: dict[str, object] = {
        "format": 2 if external_entries else 1,
        "timing_design_version": 2,
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "publication_profile": (
            "restricted-method-auditable"
            if external_entries
            else "public-replayable-synthetic"
        ),
        "contains_source_content_hashes": bool(external_entries),
        "automatic_publication": False,
        "canonical": {
            "url": CANONICAL_URL,
            "provenance_ref": CANONICAL_REF,
            "sha": CANONICAL_SHA,
        },
        "intermediate": (
            {
                "url": INTERMEDIATE_URL,
                "provenance_ref": INTERMEDIATE_REF,
                "sha": INTERMEDIATE_SHA,
            }
            if intermediate_repository
            else None
        ),
        "dumi": {
            "url": DUMI_PUBLIC_URL,
            "ref": None if external_entries else args.dumi_ref,
            "ref_recorded": not bool(external_entries),
            "sha": dumi_sha,
            "uncommitted_worktree_sources_excluded": True,
            "worktree_was_dirty": bool(worktree_status),
        },
        "dependencies": dependencies,
        "dependency_files": [
            {"path": record_path(path, output_root), "sha256": sha256_file(path)}
            for path in dependency_paths
        ],
        "harness_commit_binding": harness_commit_binding,
        "harness_files": harness_files,
        "builds": builds,
        "config": {
            "active_processors": args.active_processors,
            "allow_output_in_repo": args.allow_output_in_repo,
            "cluster_tag_xmx": (
                effective_cluster_tag_xmx if external_entries else None
            ),
            "cluster_tag_xmx_source": (
                (
                    "explicit-cluster-tag-xmx"
                    if args.cluster_tag_xmx is not None
                    else "inherited-from-xmx"
                )
                if external_entries
                else None
            ),
            "cluster_sort_command": (
                "<GNU_SORT>" if gnu_sort is not None else None
            ),
            "dumi_ref": None if external_entries else args.dumi_ref,
            "dumi_source_sha": dumi_sha,
            "input_mode": (
                "external_bam" if external_entries else "synthetic"
            ),
            "hotspot_families": (
                None if external_entries else args.hotspot_families
            ),
            "include_intermediate": args.include_intermediate,
            "keep_outputs": args.keep_outputs,
            "moderate_families_per_group": (
                None
                if external_entries
                else args.moderate_families_per_group
            ),
            "moderate_groups": (
                None if external_entries else args.moderate_groups
            ),
            "paired_pairs_per_reference": (
                None
                if external_entries
                else args.paired_pairs_per_reference
            ),
            "paired_references": paired_references,
            "profile": None if external_entries else args.profile,
            "repetitions": args.repetitions,
            "timing_design_version": 2,
            "seed": None if external_entries else args.seed,
            "selected_workloads": selected,
            "external_workload_ids": [
                entry.workload_id for entry in external_entries
            ],
            "sparse_records": sparse_records,
            "xms": args.xms,
            "xmx": args.xmx,
        },
        "external_inputs": [
            external_validation_receipts[entry.workload_id]
            for entry in external_entries
        ],
        "external_provenance_ledger": (
            external_provenance_ledger_receipt
            if external_entries
            else None
        ),
        "subprocess_environment": environment_json["subprocess_environment"],
        "jvm_options": [
            "-XX:-UsePerfData",
            "-server",
            f"-Xms{args.xms}",
            f"-Xmx{args.xmx}",
            "-Xss20m",
            f"-XX:ActiveProcessorCount={args.active_processors}",
        ],
        "cluster_tag_jvm_options": (
            cluster_tag_jvm_options if external_entries else None
        ),
    }
    runtime_identity = {
        "java": environment_json["java"],
        "javac": environment_json["javac"],
        "dependencies": [
            {"filename": item["filename"], "sha256": item["sha256"]}
            for item in dependencies
        ],
        "jvm_options": manifest["jvm_options"],
        "cluster_tag_jvm_options": manifest["cluster_tag_jvm_options"],
    }
    runtime_id = hashlib.sha256(
        json.dumps(runtime_identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest["runtime_id"] = runtime_id
    manifest["implementation_sources"] = {
        "canonical-upstream/legacy": CANONICAL_SHA,
        "dumi/off": dumi_sha,
        "dumi/on": dumi_sha,
        "dumi/auto": dumi_sha,
    }
    if intermediate_repository:
        manifest["implementation_sources"]["intermediate-pr32/legacy"] = INTERMEDIATE_SHA
    workloads: list[Workload] = []
    for entry in external_entries:
        workloads.append(
            Workload(
                "external",
                entry.workload_id,
                entry.umi_length,
                entry.paired,
                (),
                umi_separator=entry.umi_separator,
                external_input=entry,
                streaming_on_eligible=bool(
                    external_streaming_receipts[entry.workload_id]["eligible"]
                ),
            )
        )
    # Generator option contracts are intentionally explicit in the evidence.
    if "sparse" in selected:
        for records in sparse_records:
            workloads.append(
                Workload(
                    "sparse",
                    f"records-{records}",
                    12,
                    False,
                    ("sparse", "--records", str(records)),
                )
            )
    if "moderate" in selected:
        workloads.append(
            Workload(
                "moderate",
                (
                    f"groups-{args.moderate_groups}-"
                    f"families-{args.moderate_families_per_group}"
                ),
                12,
                False,
                (
                    "moderate",
                    "--groups",
                    str(args.moderate_groups),
                    "--families-per-group",
                    str(args.moderate_families_per_group),
                ),
            )
        )
    if "hotspot" in selected:
        workloads.append(
            Workload(
                "hotspot",
                f"families-{args.hotspot_families}",
                12,
                False,
                (
                    "hotspot",
                    "--families",
                    str(args.hotspot_families),
                ),
            )
        )
    if "paired" in selected:
        for references in paired_references:
            workloads.append(
                Workload(
                    "paired",
                    f"references-{references}",
                    12,
                    True,
                    (
                        "paired",
                        "--references",
                        str(references),
                        "--pairs-per-reference",
                        str(args.paired_pairs_per_reference),
                    ),
                )
            )

    manifest["workloads"] = [
        {
            "name": workload.name,
            "scale": workload.scale,
            "umi_length": workload.umi_length,
            "umi_separator": workload.umi_separator,
            "paired": workload.paired,
            "input_mode": workload.input_mode,
            "streaming_on_eligible": workload.streaming_on_eligible,
            "forced_on_contract_recorded": (
                workload.external_input is not None
            ),
            "generator_arguments": list(workload.generator_args),
            "rationale_provided": (
                bool(workload.external_input.rationale)
                if workload.external_input is not None
                else False
            ),
            "directional_oracle_gate": (
                {
                    "applicable": True,
                    "status": "pending",
                    "input_sha256": workload.external_input.bam_sha256,
                    "untimed": True,
                    "tagged_outputs_retained": False,
                    "private_oracle_streams_retained": False,
                }
                if workload.external_input is not None
                else {
                    "applicable": False,
                    "status": "not-applicable",
                }
            ),
            "pairwise_cluster_diagnostic": (
                {
                    "applicable": True,
                    "status": "pending",
                    "scope": "diagnostic-only",
                    "untimed": True,
                    "tagged_outputs_retained": False,
                    "private_partition_streams_retained": False,
                }
                if workload.external_input is not None
                else {
                    "applicable": False,
                    "status": "not-applicable",
                }
            ),
            "performance_comparability": (
                {
                    "applicable": True,
                    "status": "pending",
                    "issues": [],
                }
                if workload.external_input is not None
                else {
                    "applicable": False,
                    "status": "not-applicable",
                    "issues": [],
                }
            ),
        }
        for workload in workloads
    ]
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    design_path = output_root / "design.tsv"
    design_columns = [
        "run_id",
        "workload",
        "scale",
        "stage",
        "implementation",
        "mode",
        "repetition",
        "order",
    ]
    with design_path.open("w", encoding="utf-8", newline="") as design_handle:
        design_writer = csv.DictWriter(
            design_handle,
            fieldnames=design_columns,
            delimiter="\t",
            lineterminator="\n",
        )
        design_writer.writeheader()
        for workload_index, workload in enumerate(workloads):
            implementations = implementations_for(
                workload, intermediate_repository is not None
            )
            scheduled_cells = workload_stage_schedule(
                implementations,
                args.repetitions,
                workload_index,
            )
            for stage, cell in scheduled_cells:
                logical_id = (
                    f"{workload.name}-{workload.scale}-"
                    f"r{cell.repetition:02d}-o{cell.order:02d}-"
                    f"{cell.implementation.label}"
                )
                design_writer.writerow(
                    {
                        "run_id": f"{logical_id}-{stage}",
                        "workload": workload.name,
                        "scale": workload.scale,
                        "stage": stage,
                        "implementation": cell.implementation.name,
                        "mode": cell.implementation.mode,
                        "repetition": cell.repetition,
                        "order": cell.order,
                    }
                )

    measurement_path = output_root / "measurements.tsv"
    measurement_handle = measurement_path.open("w", encoding="utf-8", newline="")
    writer = csv.DictWriter(
        measurement_handle,
        fieldnames=MEASUREMENT_COLUMNS,
        delimiter="\t",
        lineterminator="\n",
    )
    writer.writeheader()
    measurement_handle.flush()
    directional_oracle_results: dict[
        str, tuple[dict[str, object], str]
    ] = {}
    external_cross_implementation_receipts: dict[
        str, dict[str, object]
    ] = {}

    try:
        for workload_index, workload in enumerate(workloads):
            input_root = output_root / "inputs" / workload.name / workload.scale
            input_root.mkdir(parents=True, exist_ok=True)
            oracle_output: Path | None = None
            oracle_canonical: Path | None = None
            oracle_canonical_receipt: Path | None = None
            upstream_oracle_output: Path | None = None
            upstream_oracle_canonical: Path | None = None
            upstream_oracle_canonical_receipt: Path | None = None
            implementation_oracles: dict[
                str, tuple[Path, Path, Path, dict[str, object], str]
            ] = {}
            cross_implementation_receipt: dict[str, object] = {
                "exact_match": True,
                "output_count_match": True,
                "alignment_group_output_count_match": True,
            }
            if workload.external_input is not None:
                external_input = workload.external_input
                bam_input = external_runtime_inputs[workload.scale]
                input_hash = sha256_file(bam_input)
                if input_hash != external_input.bam_sha256:
                    raise BenchmarkError(
                        f"private external BAM snapshot changed for workload "
                        f"{workload.scale!r}"
                    )
                input_index = adjacent_bam_index(bam_input)
                input_index_hash = sha256_file(input_index)
                validation_receipt = external_validation_receipts[workload.scale]

                oracle_root = (
                    output_root / "oracles" / workload.name / workload.scale
                )
                oracle_root.mkdir(parents=True, exist_ok=True)
                alignment_group_mode = (
                    "paired" if workload.paired else "single-end"
                )

                dumi_oracle_root = oracle_root / "dumi-off"
                dumi_oracle_root.mkdir()
                oracle_output = dumi_oracle_root / "output.private.bam"
                oracle_canonical = (
                    dumi_oracle_root / "records.sorted.private"
                )
                oracle_canonical_receipt = (
                    dumi_oracle_root / "canonical-receipt.private"
                )
                dumi_oracle_tmp = dumi_oracle_root / "java-tmp"
                dumi_oracle_tmp.mkdir()
                dumi_oracle_command = build_java_bam_command(
                    java=java,
                    jvm_options=jvm_options,
                    java_tmp=dumi_oracle_tmp,
                    classes_root=classes["dumi"],
                    common_classpath=common_classpath,
                    bam_input=bam_input,
                    output=oracle_output,
                    workload=workload,
                    source_key="dumi",
                    streaming_mode="off",
                )
                (dumi_oracle_root / "command.txt").write_text(
                    command_text(dumi_oracle_command) + "\n", encoding="utf-8"
                )
                dumi_oracle_completed = run_command(
                    dumi_oracle_command,
                    stdout_path=dumi_oracle_root / "stdout.txt",
                    stderr_path=dumi_oracle_root / "stderr.txt",
                    check=False,
                )
                if dumi_oracle_completed.returncode != 0:
                    raise BenchmarkError(
                        f"dUMI-off oracle failed for external workload "
                        f"{workload.scale!r}"
                    )
                oracle_inspection = inspect_output(
                    checker=checker,
                    python=python,
                    samtools=samtools,
                    output=oracle_output,
                    temporary_root=dumi_oracle_root / "semantic-tmp",
                    canonical_output=oracle_canonical,
                    canonical_receipt_output=oracle_canonical_receipt,
                    alignment_group_mode=alignment_group_mode,
                )
                if oracle_inspection["sort_order"] != "coordinate":
                    raise BenchmarkError(
                        f"dUMI-off oracle was not coordinate-sorted for "
                        f"external workload {workload.scale!r}"
                    )
                if (
                    oracle_inspection["reference_sequences"]
                    != validation_receipt["reference_sequences"]
                    or oracle_inspection["reference_dictionary_sha256"]
                    != validation_receipt["reference_dictionary_sha256"]
                ):
                    raise BenchmarkError(
                        f"dUMI-off oracle changed the reference dictionary "
                        f"for external workload {workload.scale!r}"
                    )
                (dumi_oracle_root / "inspection.json").write_text(
                    json.dumps(oracle_inspection, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )

                upstream_oracle_root = oracle_root / "canonical-upstream"
                upstream_oracle_root.mkdir()
                upstream_oracle_output = (
                    upstream_oracle_root / "output.private.bam"
                )
                upstream_oracle_canonical = (
                    upstream_oracle_root / "records.sorted.private"
                )
                upstream_oracle_canonical_receipt = (
                    upstream_oracle_root / "canonical-receipt.private"
                )
                upstream_oracle_tmp = upstream_oracle_root / "java-tmp"
                upstream_oracle_tmp.mkdir()
                upstream_oracle_command = build_java_bam_command(
                    java=java,
                    jvm_options=jvm_options,
                    java_tmp=upstream_oracle_tmp,
                    classes_root=classes["upstream"],
                    common_classpath=common_classpath,
                    bam_input=bam_input,
                    output=upstream_oracle_output,
                    workload=workload,
                    source_key="upstream",
                    streaming_mode=None,
                )
                (upstream_oracle_root / "command.txt").write_text(
                    command_text(upstream_oracle_command) + "\n",
                    encoding="utf-8",
                )
                upstream_oracle_completed = run_command(
                    upstream_oracle_command,
                    stdout_path=upstream_oracle_root / "stdout.txt",
                    stderr_path=upstream_oracle_root / "stderr.txt",
                    check=False,
                )
                if upstream_oracle_completed.returncode != 0:
                    raise BenchmarkError(
                        f"canonical-upstream oracle failed for external workload "
                        f"{workload.scale!r}"
                    )
                upstream_oracle_inspection = inspect_output(
                    checker=checker,
                    python=python,
                    samtools=samtools,
                    output=upstream_oracle_output,
                    temporary_root=upstream_oracle_root / "semantic-tmp",
                    reference=oracle_output,
                    reference_canonical=oracle_canonical,
                    reference_canonical_receipt=oracle_canonical_receipt,
                    reference_canonical_sha256=str(
                        oracle_inspection["semantic_sha256"]
                    ),
                    canonical_output=upstream_oracle_canonical,
                    canonical_receipt_output=(
                        upstream_oracle_canonical_receipt
                    ),
                    alignment_group_mode=alignment_group_mode,
                )
                if upstream_oracle_inspection["sort_order"] != "coordinate":
                    raise BenchmarkError(
                        f"canonical-upstream oracle was not coordinate-sorted for "
                        f"external workload {workload.scale!r}"
                    )
                cross_implementation_receipt = (
                    cross_implementation_oracle_receipt(
                        candidate=upstream_oracle_inspection,
                        reference=oracle_inspection,
                        context=f"external workload {workload.scale!r}",
                    )
                )
                external_cross_implementation_receipts[workload.scale] = dict(
                    cross_implementation_receipt
                )
                (upstream_oracle_root / "inspection.json").write_text(
                    json.dumps(
                        upstream_oracle_inspection, indent=2, sort_keys=True
                    )
                    + "\n",
                    encoding="utf-8",
                )
                (oracle_root / "cross-implementation-receipt.json").write_text(
                    json.dumps(
                        cross_implementation_receipt,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                implementation_oracles = {
                    "dumi": (
                        oracle_output,
                        oracle_canonical,
                        oracle_canonical_receipt,
                        oracle_inspection,
                        "dumi-off",
                    ),
                    "upstream": (
                        upstream_oracle_output,
                        upstream_oracle_canonical,
                        upstream_oracle_canonical_receipt,
                        upstream_oracle_inspection,
                        "canonical-upstream",
                    ),
                }

                expected_output_records = int(
                    oracle_inspection["output_records"]
                )
                expected_output_sha256 = str(
                    oracle_inspection["semantic_sha256"]
                )
                expected_reference_sequences = int(
                    oracle_inspection["reference_sequences"]
                )
                expected_reference_dictionary_sha256 = str(
                    oracle_inspection["reference_dictionary_sha256"]
                )
                input_manifest = {
                    "input_mode": "external_bam",
                    "bam": {
                        "bytes": validation_receipt["bytes"],
                        "sha256": input_hash,
                        "path_recorded": False,
                    },
                    "validation": {
                        "quickcheck_status": "pass",
                        "declared_sort_order": "coordinate",
                        "temporary_index_validation": "pass",
                    },
                    "workload_id": workload.scale,
                    "paired": workload.paired,
                    "umi_length": workload.umi_length,
                    "umi_separator": workload.umi_separator,
                    "rationale_provided": bool(external_input.rationale),
                    "oracles": {
                        "dumi": {
                            "implementation": "dumi",
                            "mode": "off",
                            "source_sha": dumi_sha,
                            "kind": "untimed_exact_implementation_oracle",
                            "timed": False,
                            "output_retained": False,
                            "output_records": expected_output_records,
                            "semantic_sha256": expected_output_sha256,
                            "reference_sequences": expected_reference_sequences,
                            "reference_dictionary_sha256": (
                                expected_reference_dictionary_sha256
                            ),
                        },
                        "canonical_upstream": {
                            "implementation": "canonical-upstream",
                            "source_sha": CANONICAL_SHA,
                            "kind": "untimed_exact_implementation_oracle",
                            "timed": False,
                            "output_retained": False,
                            "output_records": upstream_oracle_inspection[
                                "output_records"
                            ],
                            "semantic_sha256": upstream_oracle_inspection[
                                "semantic_sha256"
                            ],
                            "reference_sequences": upstream_oracle_inspection[
                                "reference_sequences"
                            ],
                            "reference_dictionary_sha256": (
                                upstream_oracle_inspection[
                                    "reference_dictionary_sha256"
                                ]
                            ),
                        },
                    },
                    "cross_implementation_diagnostic": (
                        cross_implementation_receipt
                    ),
                }
            else:
                sam_input = input_root / "input.sam"
                bam_input = input_root / "input.bam"
                metadata_path = input_root / "metadata.json"
                generator_command = [
                    python,
                    generator,
                    *workload.generator_args,
                    "--seed",
                    str(args.seed),
                    "--output",
                    sam_input,
                    "--metadata",
                    metadata_path,
                ]
                (input_root / "generator-command.txt").write_text(
                    command_text(generator_command) + "\n", encoding="utf-8"
                )
                run_command(
                    generator_command,
                    stdout_path=input_root / "generator-stdout.txt",
                    stderr_path=input_root / "generator-stderr.txt",
                )
                try:
                    workload_metadata = json.loads(
                        metadata_path.read_text(encoding="utf-8")
                    )
                    expected_output_records = int(
                        workload_metadata["expected_output"]["records"]
                    )
                    expected_output_sha256 = str(
                        workload_metadata["expected_output"][
                            "canonical_record_sha256"
                        ]
                    )
                    generated_records = int(workload_metadata["input"]["records"])
                    generated_bytes = int(workload_metadata["input"]["bytes"])
                    expected_reference_sequences = int(
                        workload_metadata["input"]["reference_sequences"]
                    )
                    expected_reference_dictionary_sha256 = str(
                        workload_metadata["input"]["reference_dictionary_sha256"]
                    )
                    generated_umi_length = int(
                        workload_metadata["parameters"]["umi_length"]
                    )
                except (
                    KeyError,
                    TypeError,
                    ValueError,
                    json.JSONDecodeError,
                ) as error:
                    raise BenchmarkError(
                        f"generator emitted malformed metadata for "
                        f"{workload.name}/{workload.scale}"
                    ) from error
                if (
                    workload_metadata["input"]["sha256"] != sha256_file(sam_input)
                    or generated_bytes != sam_input.stat().st_size
                    or generated_umi_length != workload.umi_length
                    or len(expected_output_sha256) != 64
                    or len(expected_reference_dictionary_sha256) != 64
                ):
                    raise BenchmarkError(
                        f"generator receipt mismatch for "
                        f"{workload.name}/{workload.scale}"
                    )
                run_command(
                    [samtools, "view", "-b", "-o", bam_input, sam_input],
                    stdout_path=input_root / "samtools-view-stdout.txt",
                    stderr_path=input_root / "samtools-view-stderr.txt",
                )
                run_command(
                    [samtools, "index", bam_input],
                    stdout_path=input_root / "samtools-index-stdout.txt",
                    stderr_path=input_root / "samtools-index-stderr.txt",
                )
                input_hash = sha256_file(bam_input)
                input_index = adjacent_bam_index(bam_input)
                input_index_hash = sha256_file(input_index)
                input_manifest = {
                    "sam": {
                        "path": record_path(sam_input, output_root),
                        "bytes": sam_input.stat().st_size,
                        "sha256": sha256_file(sam_input),
                    },
                    "bam": {
                        "path": record_path(bam_input, output_root),
                        "bytes": bam_input.stat().st_size,
                        "sha256": input_hash,
                    },
                    "index": {
                        "path": record_path(
                            Path(str(bam_input) + ".bai"), output_root
                        ),
                        "bytes": Path(str(bam_input) + ".bai").stat().st_size,
                        "sha256": sha256_file(Path(str(bam_input) + ".bai")),
                    },
                    "generator_command": command_text(generator_command),
                    "input_records": generated_records,
                    "expected_output_records": expected_output_records,
                    "expected_output_semantic_sha256": expected_output_sha256,
                    "expected_reference_sequences": expected_reference_sequences,
                    "expected_reference_dictionary_sha256": (
                        expected_reference_dictionary_sha256
                    ),
                }
            (input_root / "hashes.json").write_text(
                json.dumps(input_manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            implementations = implementations_for(
                workload, intermediate_repository is not None
            )
            warmup_routes: dict[str, str] = {}
            warmup_sort_orders: dict[str, str] = {}

            # Untimed page-cache/class-loading smoke. Each Java invocation is still fresh.
            warmup_order = latin_order(implementations, workload_index)
            for warm_order, implementation in enumerate(warmup_order, 1):
                warm_root = (
                    output_root
                    / "warmups"
                    / workload.name
                    / workload.scale
                    / f"{warm_order:02d}-{implementation.label}"
                )
                warm_root.mkdir(parents=True, exist_ok=True)
                warm_output = warm_root / "output.bam"
                java_tmp = warm_root / "java-tmp"
                java_tmp.mkdir()
                java_command = build_java_bam_command(
                    java=java,
                    jvm_options=jvm_options,
                    java_tmp=java_tmp,
                    classes_root=classes[implementation.source_key],
                    common_classpath=common_classpath,
                    bam_input=bam_input,
                    output=warm_output,
                    workload=workload,
                    source_key=implementation.source_key,
                    streaming_mode=(
                        implementation.mode
                        if implementation.name == "dumi"
                        else None
                    ),
                )
                (warm_root / "command.txt").write_text(
                    command_text(java_command) + "\n", encoding="utf-8"
                )
                completed = run_command(
                    java_command,
                    stdout_path=warm_root / "stdout.txt",
                    stderr_path=warm_root / "stderr.txt",
                    check=False,
                )
                if completed.returncode != 0:
                    raise BenchmarkError(
                        f"warm-up failed for {workload.name}/{implementation.label}"
                    )
                if workload.external_input is not None:
                    (
                        exact_reference,
                        exact_reference_canonical,
                        exact_reference_canonical_receipt,
                        exact_expectation,
                        _,
                    ) = implementation_oracles[implementation.source_key]
                else:
                    exact_reference = oracle_output
                    exact_reference_canonical = oracle_canonical
                    exact_reference_canonical_receipt = (
                        oracle_canonical_receipt
                    )
                    exact_expectation = {
                        "output_records": expected_output_records,
                        "semantic_sha256": expected_output_sha256,
                        "reference_sequences": expected_reference_sequences,
                        "reference_dictionary_sha256": (
                            expected_reference_dictionary_sha256
                        ),
                    }
                warm_inspection = inspect_output(
                    checker=checker,
                    python=python,
                    samtools=samtools,
                    output=warm_output,
                    temporary_root=warm_root / "semantic-tmp",
                    reference=exact_reference,
                    reference_canonical=exact_reference_canonical,
                    reference_canonical_receipt=(
                        exact_reference_canonical_receipt
                    ),
                    reference_canonical_sha256=(
                        str(exact_expectation["semantic_sha256"])
                        if exact_reference_canonical is not None
                        else None
                    ),
                    alignment_group_mode=(
                        "paired" if workload.paired else "single-end"
                    ),
                )
                warm_inspection["actual_route"] = observed_execution_route(
                    stdout_path=warm_root / "stdout.txt",
                    stderr_path=warm_root / "stderr.txt",
                    sort_order=warm_inspection["sort_order"],
                    implementation_name=implementation.name,
                    requested_mode=implementation.mode,
                    paired=workload.paired,
                    context=(
                        f"warm-up for {workload.name}/{workload.scale}/"
                        f"{implementation.label}"
                    ),
                )
                validate_external_route_contract(
                    workload=workload,
                    implementation_name=implementation.name,
                    requested_mode=implementation.mode,
                    observed_route=str(warm_inspection["actual_route"]),
                    context=(
                        f"warm-up for {workload.name}/{workload.scale}/"
                        f"{implementation.label}"
                    ),
                )
                warmup_routes[implementation.label] = str(
                    warm_inspection["actual_route"]
                )
                warmup_sort_orders[implementation.label] = str(
                    warm_inspection["sort_order"]
                )
                if workload.external_input is None:
                    expected_warm_route = (
                        "coordinate"
                        if implementation.name != "dumi"
                        else "off"
                        if implementation.mode == "off"
                        else "off-ineligible"
                        if workload.paired
                        else "streaming"
                    )
                    if warm_inspection["actual_route"] != expected_warm_route:
                        raise BenchmarkError(
                            f"unexpected warm-up route for synthetic "
                            f"{workload.name}/{workload.scale}/"
                            f"{implementation.label}"
                        )
                if workload.external_input is not None:
                    (warm_root / "inspection.json").write_text(
                        json.dumps(warm_inspection, indent=2, sort_keys=True)
                        + "\n",
                        encoding="utf-8",
                    )
                if (
                    int(warm_inspection["output_records"])
                    != int(exact_expectation["output_records"])
                    or str(warm_inspection["semantic_sha256"])
                    != str(exact_expectation["semantic_sha256"])
                    or int(warm_inspection["reference_sequences"])
                    != int(exact_expectation["reference_sequences"])
                    or str(warm_inspection["reference_dictionary_sha256"])
                    != str(exact_expectation["reference_dictionary_sha256"])
                    or (
                        exact_reference is not None
                        and warm_inspection["exact_oracle_match"] is not True
                    )
                ):
                    raise BenchmarkError(
                        f"warm-up output does not match the workload oracle for "
                        f"{workload.name}/{workload.scale}/{implementation.label}"
                    )
                if not args.keep_outputs:
                    warm_output.unlink(missing_ok=True)

            contract_root = output_root / "contracts" / workload.name / workload.scale
            contract_root.mkdir(parents=True, exist_ok=True)
            default_output = contract_root / "default-auto.bam"
            default_tmp = contract_root / "java-tmp"
            default_tmp.mkdir()
            default_command = build_java_bam_command(
                java=java,
                jvm_options=jvm_options,
                java_tmp=default_tmp,
                classes_root=classes["dumi"],
                common_classpath=common_classpath,
                bam_input=bam_input,
                output=default_output,
                workload=workload,
                source_key="dumi",
                streaming_mode=None,
            )
            (contract_root / "default-command.txt").write_text(
                command_text(default_command) + "\n", encoding="utf-8"
            )
            default_completed = run_command(
                default_command,
                stdout_path=contract_root / "default-stdout.txt",
                stderr_path=contract_root / "default-stderr.txt",
                check=False,
            )
            if default_completed.returncode != 0:
                raise BenchmarkError(
                    f"default/no-flag contract failed for {workload.name}/{workload.scale}"
                )
            default_inspection = inspect_output(
                checker=checker,
                python=python,
                samtools=samtools,
                output=default_output,
                temporary_root=contract_root / "semantic-tmp",
                reference=oracle_output,
                reference_canonical=oracle_canonical,
                reference_canonical_receipt=oracle_canonical_receipt,
                reference_canonical_sha256=(
                    expected_output_sha256
                    if oracle_canonical is not None
                    else None
                ),
                alignment_group_mode=(
                    "paired" if workload.paired else "single-end"
                ),
            )
            default_inspection["actual_route"] = observed_execution_route(
                stdout_path=contract_root / "default-stdout.txt",
                stderr_path=contract_root / "default-stderr.txt",
                sort_order=default_inspection["sort_order"],
                implementation_name="dumi",
                requested_mode="auto",
                paired=workload.paired,
                context=(
                    f"default/no-flag contract for "
                    f"{workload.name}/{workload.scale}"
                ),
            )
            validate_external_route_contract(
                workload=workload,
                implementation_name="dumi",
                requested_mode="auto",
                observed_route=str(default_inspection["actual_route"]),
                context=(
                    f"default/no-flag contract for "
                    f"{workload.name}/{workload.scale}"
                ),
            )
            if workload.external_input is None:
                expected_default_route = (
                    "off-ineligible" if workload.paired else "streaming"
                )
                if default_inspection["actual_route"] != expected_default_route:
                    raise BenchmarkError(
                        f"unexpected default route for synthetic "
                        f"{workload.name}/{workload.scale}"
                    )
            (contract_root / "default-inspection.json").write_text(
                json.dumps(default_inspection, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            if (
                int(default_inspection["output_records"]) != expected_output_records
                or str(default_inspection["semantic_sha256"])
                != expected_output_sha256
                or int(default_inspection["reference_sequences"])
                != expected_reference_sequences
                or str(default_inspection["reference_dictionary_sha256"])
                != expected_reference_dictionary_sha256
                or (
                    oracle_output is not None
                    and default_inspection["exact_oracle_match"] is not True
                )
            ):
                raise BenchmarkError(
                    f"default/no-flag output does not match the workload oracle for "
                    f"{workload.name}/{workload.scale}"
                )
            if workload.paired and workload.external_input is None:
                reject_output = contract_root / "forced-on-should-not-exist.bam"
                reject_command = [
                    *default_command,
                    "--streaming-mode",
                    "on",
                ]
                reject_command[reject_command.index(default_output)] = reject_output
                (contract_root / "forced-on-command.txt").write_text(
                    command_text(reject_command) + "\n", encoding="utf-8"
                )
                rejected = run_command(
                    reject_command,
                    stdout_path=contract_root / "forced-on-stdout.txt",
                    stderr_path=contract_root / "forced-on-stderr.txt",
                    check=False,
                )
                if rejected.returncode == 0 or reject_output.exists():
                    raise BenchmarkError(
                        f"paired forced-streaming rejection contract failed for {workload.scale}"
                    )

            scheduled_cells = workload_stage_schedule(
                implementations,
                args.repetitions,
                workload_index,
            )
            capacity_receipt_path = input_root / "stage-scratch-capacity.json"
            capacity_receipt = require_stage_scratch_capacity(
                output_root=output_root,
                bam_input=bam_input,
                treatments_per_block=len(implementations),
                directional_oracle_record_count=(
                    int(
                        external_validation_receipts[workload.scale][
                            "total_records"
                        ]
                    )
                    if workload.external_input is not None
                    else None
                ),
                directional_oracle_paired=workload.paired,
                directional_oracle_umi_length=(
                    workload.umi_length
                    if workload.external_input is not None
                    else None
                ),
                receipt_path=capacity_receipt_path,
            )
            workload_manifest_entries = manifest.get("workloads")
            if not isinstance(workload_manifest_entries, list):
                raise BenchmarkError("manifest workload registry is invalid")
            workload_manifest_entry = next(
                (
                    entry
                    for entry in workload_manifest_entries
                    if isinstance(entry, dict)
                    and entry.get("name") == workload.name
                    and entry.get("scale") == workload.scale
                ),
                None,
            )
            if not isinstance(workload_manifest_entry, dict):
                raise BenchmarkError(
                    f"manifest workload entry is missing for "
                    f"{workload.name}/{workload.scale}"
                )
            if workload.external_input is not None:
                cross_receipt = external_cross_implementation_receipts.get(
                    workload.scale
                )
                if not isinstance(cross_receipt, dict):
                    raise BenchmarkError(
                        f"cross-implementation diagnostic is missing for "
                        f"{workload.scale!r}"
                    )
                output_count_match = (
                    cross_receipt.get("output_count_match") is True
                )
                workload_manifest_entry["performance_comparability"] = {
                    "applicable": True,
                    "status": (
                        "comparable"
                        if output_count_match
                        else "not_comparable"
                    ),
                    "issues": (
                        []
                        if output_count_match
                        else [NONCOMPARABLE_OUTPUT_COUNT_ISSUE]
                    ),
                    "cross_implementation_output_count_match": (
                        output_count_match
                    ),
                    "cross_implementation_exact_match": (
                        cross_receipt.get("exact_match") is True
                    ),
                    "cross_implementation_alignment_group_output_count_match": (
                        cross_receipt.get(
                            "alignment_group_output_count_match"
                        )
                        is True
                    ),
                }
            timing_order_family = (
                "williams-first-order-balanced"
                if len(implementations) % 2 == 0
                else "cyclic-latin-fallback-nonreportable"
            )
            publication_grade_schedule = (
                workload.external_input is not None
                and len(implementations) == 4
                and args.repetitions == 8
            )
            workload_manifest_entry["timing_stage_schedule"] = {
                "timing_design_version": 2,
                "scope": "per-workload",
                "execution_order": list(MEASURED_STAGES),
                "treatments": len(implementations),
                "repetitions": args.repetitions,
                "order_family": timing_order_family,
                "complete_order_cycles": (
                    args.repetitions % len(implementations) == 0
                ),
                "publication_grade_external_schedule": (
                    publication_grade_schedule
                ),
                "raw_cells": sum(
                    stage == "raw" for stage, _ in scheduled_cells
                ),
                "end_to_end_ready_cells": sum(
                    stage == "end_to_end_ready"
                    for stage, _ in scheduled_cells
                ),
                "raw_order_offset": workload_index,
                "end_to_end_ready_order": (
                    "independent-stage-offset"
                ),
                "end_to_end_ready_order_offset": workload_index + 1,
                "cross_stage_order_matching_required": False,
                "fresh_deduplication_per_stage_cell": True,
                "validation_and_deletion": (
                    "after-complete-repetition-block"
                ),
                "capacity_receipt": record_path(
                    capacity_receipt_path, output_root
                ),
                "capacity_status": capacity_receipt["status"],
                "capacity_required_available_bytes": capacity_receipt[
                    "required_available_bytes"
                ],
                "capacity_available_bytes": capacity_receipt[
                    "available_bytes"
                ],
                "capacity_timing_peak_stage_bytes": capacity_receipt[
                    "timing_peak_stage_bytes"
                ],
                "capacity_directional_oracle_peak_stage_bytes": (
                    capacity_receipt[
                        "directional_oracle_peak_stage_bytes"
                    ]
                ),
            }
            if (
                workload.external_input is not None
                and not publication_grade_schedule
            ):
                print(
                    "warning: external timing schedule is exploratory; "
                    "publication-grade v2 evidence requires four treatments "
                    "and exactly eight repetitions",
                    file=sys.stderr,
                )
            (output_root / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            def time_cell(
                stage: str, cell: ScheduledCell
            ) -> PendingTimedCell:
                implementation = cell.implementation
                if workload.external_input is not None:
                    (
                        exact_reference,
                        exact_reference_canonical,
                        exact_reference_canonical_receipt,
                        exact_expectation,
                        oracle_implementation,
                    ) = implementation_oracles[
                        implementation.source_key
                    ]
                else:
                    exact_reference = oracle_output
                    exact_reference_canonical = oracle_canonical
                    exact_reference_canonical_receipt = (
                        oracle_canonical_receipt
                    )
                    exact_expectation = {
                        "output_records": expected_output_records,
                        "semantic_sha256": expected_output_sha256,
                        "reference_sequences": expected_reference_sequences,
                        "reference_dictionary_sha256": (
                            expected_reference_dictionary_sha256
                        ),
                    }
                    oracle_implementation = "generator"
                return time_benchmark_cell(
                    stage=stage,
                    cell=cell,
                    workload=workload,
                    output_root=output_root,
                    java=java,
                    jvm_options=jvm_options,
                    classes=classes,
                    common_classpath=common_classpath,
                    bam_input=bam_input,
                    input_hash=input_hash,
                    input_index_hash=input_index_hash,
                    samtools=samtools,
                    gnu_time=gnu_time,
                    expected_raw_sort_order=warmup_sort_orders[
                        implementation.label
                    ],
                    expected_route=warmup_routes[implementation.label],
                    exact_reference=exact_reference,
                    exact_reference_canonical=exact_reference_canonical,
                    exact_reference_canonical_receipt=(
                        exact_reference_canonical_receipt
                    ),
                    exact_expectation=exact_expectation,
                    oracle_implementation=oracle_implementation,
                )

            def validate_cell(
                pending_object: object,
            ) -> dict[str, object]:
                if not isinstance(pending_object, PendingTimedCell):
                    raise BenchmarkError("timing block returned invalid state")
                row = validate_benchmark_cell(
                    pending=pending_object,
                    workload=workload,
                    output_root=output_root,
                    checker=checker,
                    python=python,
                    samtools=samtools,
                    input_hash=input_hash,
                    cross_implementation_receipt=(
                        cross_implementation_receipt
                    ),
                    keep_outputs=args.keep_outputs,
                )
                writer.writerow(row)
                measurement_handle.flush()
                return row

            group_results = execute_timing_blocks(
                scheduled_cells=scheduled_cells,
                repetitions=args.repetitions,
                treatments_per_block=len(implementations),
                time_cell=time_cell,
                validate_cell=validate_cell,
            )

            if workload.external_input is not None:
                dumi_hashes = {
                    str(row["semantic_sha256"])
                    for row in group_results
                    if row["implementation"] == "dumi"
                }
                upstream_hashes = {
                    str(row["semantic_sha256"])
                    for row in group_results
                    if row["implementation"] == "canonical-upstream"
                }
                if dumi_hashes != {
                    str(implementation_oracles["dumi"][3]["semantic_sha256"])
                } or upstream_hashes != {
                    str(
                        implementation_oracles["upstream"][3][
                            "semantic_sha256"
                        ]
                    )
                }:
                    raise BenchmarkError(
                        f"implementation-specific oracle mismatch for "
                        f"{workload.name}/{workload.scale}"
                    )
                if str(default_inspection["semantic_sha256"]) not in dumi_hashes:
                    raise BenchmarkError(
                        f"default/no-flag dUMI output mismatch for "
                        f"{workload.name}/{workload.scale}"
                    )
            else:
                semantic_hashes = {
                    str(row["semantic_sha256"]) for row in group_results
                }
                record_counts = {
                    str(row["output_records"]) for row in group_results
                }
                reference_hashes = {
                    str(row["reference_dictionary_sha256"])
                    for row in group_results
                }
                reference_counts = {
                    str(row["reference_sequences"]) for row in group_results
                }
                if (
                    len(semantic_hashes) != 1
                    or len(record_counts) != 1
                    or len(reference_hashes) != 1
                    or len(reference_counts) != 1
                ):
                    raise BenchmarkError(
                        f"record or reference-dictionary mismatch for "
                        f"{workload.name}/{workload.scale}"
                    )
                if (
                    str(default_inspection["semantic_sha256"])
                    not in semantic_hashes
                ):
                    raise BenchmarkError(
                        f"default/no-flag output mismatch for "
                        f"{workload.name}/{workload.scale}"
                    )
            if not args.keep_outputs:
                default_output.unlink(missing_ok=True)
            if workload.external_input is not None:
                verify_external_timing_snapshot(
                    entry=workload.external_input,
                    snapshot_bam=bam_input,
                    validation_receipt=external_validation_receipts[
                        workload.scale
                    ],
                )
                if oracle_output is not None:
                    oracle_output.unlink(missing_ok=True)
                if oracle_canonical is not None:
                    oracle_canonical.unlink(missing_ok=True)
                if oracle_canonical_receipt is not None:
                    oracle_canonical_receipt.unlink(missing_ok=True)
                if upstream_oracle_output is not None:
                    upstream_oracle_output.unlink(missing_ok=True)
                if upstream_oracle_canonical is not None:
                    upstream_oracle_canonical.unlink(missing_ok=True)
                if upstream_oracle_canonical_receipt is not None:
                    upstream_oracle_canonical_receipt.unlink(
                        missing_ok=True
                    )

        # Run all untimed tag-derived correctness gates only after every timed
        # workload has finished, so their extra Java and external-sort I/O
        # cannot perturb a later timing cell.
        for workload in (
            candidate
            for candidate in workloads
            if candidate.external_input is not None
        ):
            assert workload.external_input is not None
            bam_input = external_runtime_inputs[workload.scale]
            verify_external_timing_snapshot(
                entry=workload.external_input,
                snapshot_bam=bam_input,
                validation_receipt=external_validation_receipts[
                    workload.scale
                ],
            )
            directional_receipt_path = (
                output_root
                / "oracles"
                / workload.name
                / workload.scale
                / "directional-oracle-receipt.json"
            )
            pairwise_receipt_path = directional_receipt_path.with_name(
                "pairwise-cluster-diagnostic-receipt.json"
            )
            directional_receipt_record = record_path(
                directional_receipt_path, output_root
            )
            pairwise_receipt_record = record_path(
                pairwise_receipt_path, output_root
            )
            workload_manifest_entries = manifest.get("workloads")
            if not isinstance(workload_manifest_entries, list):
                raise BenchmarkError("manifest workload registry is invalid")
            workload_manifest_entry = next(
                (
                    entry
                    for entry in workload_manifest_entries
                    if isinstance(entry, dict)
                    and entry.get("name") == workload.name
                    and entry.get("scale") == workload.scale
                ),
                None,
            )
            if not isinstance(workload_manifest_entry, dict):
                raise BenchmarkError(
                    f"manifest workload entry is missing for "
                    f"{workload.name}/{workload.scale}"
                )
            if gnu_sort is None:
                raise BenchmarkError(
                    "GNU sort was not resolved for the external "
                    "directional-oracle gate"
                )
            post_timing_capacity_path = (
                output_root
                / "inputs"
                / workload.name
                / workload.scale
                / "post-timing-oracle-scratch-capacity.json"
            )
            post_timing_capacity = require_stage_scratch_capacity(
                output_root=output_root,
                bam_input=bam_input,
                treatments_per_block=len(
                    implementations_for(workload, False)
                ),
                directional_oracle_record_count=int(
                    external_validation_receipts[workload.scale][
                        "total_records"
                    ]
                ),
                directional_oracle_paired=workload.paired,
                directional_oracle_umi_length=workload.umi_length,
                receipt_path=post_timing_capacity_path,
                directional_oracle_only=True,
            )
            try:
                gate_results = run_external_directional_oracle_gate(
                    workload=workload,
                    bam_input=bam_input,
                    private_root=(
                        output_root
                        / "private-directional-oracle"
                        / workload.name
                        / workload.scale
                    ),
                    directional_receipt_path=directional_receipt_path,
                    pairwise_receipt_path=pairwise_receipt_path,
                    java=java,
                    jvm_options=cluster_tag_jvm_options,
                    classes=classes,
                    common_classpath=common_classpath,
                    python=python,
                    directional_checker=directional_oracle_checker,
                    pairwise_checker=cluster_partition_checker,
                    samtools=samtools,
                    sort_command=gnu_sort,
                )
            except BenchmarkError:
                workload_manifest_entry["directional_oracle_gate"] = {
                    "applicable": True,
                    "status": "error",
                    "input_sha256": workload.external_input.bam_sha256,
                    "untimed": True,
                    "tagged_outputs_retained": False,
                    "private_oracle_streams_retained": False,
                    "receipt": (
                        directional_receipt_record
                        if directional_receipt_path.is_file()
                        else None
                    ),
                }
                workload_manifest_entry["pairwise_cluster_diagnostic"] = {
                    "applicable": True,
                    "status": "error",
                    "scope": "diagnostic-only",
                    "untimed": True,
                    "tagged_outputs_retained": False,
                    "private_partition_streams_retained": False,
                    "receipt": (
                        pairwise_receipt_record
                        if pairwise_receipt_path.is_file()
                        else None
                    ),
                }
                (output_root / "manifest.json").write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                raise

            directional_receipt = gate_results["directional"]
            pairwise_receipt = gate_results["pairwise"]
            gate = directional_receipt["gate"]
            diagnostics = directional_receipt["diagnostics"]
            assert isinstance(gate, dict)
            assert isinstance(diagnostics, dict)
            directional_gate_pass = (
                gate["directional_oracle_gate_pass"] is True
            )
            workload_manifest_entry["directional_oracle_gate"] = {
                "applicable": True,
                "status": "pass" if directional_gate_pass else "fail",
                "input_sha256": workload.external_input.bam_sha256,
                **gate,
                "diagnostics": diagnostics,
                "methods": directional_receipt["methods"],
                "input": "verified-private-timing-snapshot",
                "receipt": directional_receipt_record,
                "receipt_sha256": sha256_file(directional_receipt_path),
                "untimed": True,
                "tagged_outputs_retained": False,
                "private_oracle_streams_retained": False,
                "post_timing_capacity_receipt": record_path(
                    post_timing_capacity_path, output_root
                ),
                "post_timing_capacity_required_available_bytes": (
                    post_timing_capacity["required_available_bytes"]
                ),
                "post_timing_capacity_available_bytes": (
                    post_timing_capacity["available_bytes"]
                ),
            }
            pairwise_match = pairwise_receipt.get("equivalent") is True
            workload_manifest_entry["pairwise_cluster_diagnostic"] = {
                "applicable": True,
                "status": "match" if pairwise_match else "difference",
                "scope": "diagnostic-only",
                "equivalent": pairwise_match,
                "partition_equivalent": (
                    pairwise_receipt.get("partition_equivalent") is True
                ),
                "reference_dictionary_equivalent": (
                    pairwise_receipt.get(
                        "reference_dictionary_equivalent"
                    )
                    is True
                ),
                "read_group_dictionary_equivalent": (
                    pairwise_receipt.get(
                        "read_group_dictionary_equivalent"
                    )
                    is True
                ),
                "receipt": pairwise_receipt_record,
                "receipt_sha256": sha256_file(pairwise_receipt_path),
                "untimed": True,
                "tagged_outputs_retained": False,
                "private_partition_streams_retained": False,
            }
            (output_root / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            directional_oracle_results[workload.scale] = (
                directional_receipt,
                directional_receipt_record,
            )
            if not directional_gate_pass:
                raise BenchmarkError(
                    f"directional-oracle gate failed for external workload "
                    f"{workload.scale!r}"
                )
            verify_external_timing_snapshot(
                entry=workload.external_input,
                snapshot_bam=bam_input,
                validation_receipt=external_validation_receipts[
                    workload.scale
                ],
            )
    finally:
        measurement_handle.close()
        if external_entries:
            sanitize_external_failure(output_root)

    if external_entries:
        annotate_external_directional_oracle_measurements(
            measurement_path=measurement_path,
            results=directional_oracle_results,
        )
    validate_design_completion(design_path, measurement_path)
    summary_path = output_root / "summary.tsv"
    correctness_path = output_root / "correctness.tsv"
    comparisons_path = output_root / "comparisons.tsv"
    run_command(
        [
            python,
            summarizer,
            measurement_path,
            "--output",
            summary_path,
            "--expected-repetitions",
            str(args.repetitions),
            "--design-tsv",
            design_path,
            "--correctness-output",
            correctness_path,
            "--comparisons-output",
            comparisons_path,
        ],
        stdout_path=output_root / "summarizer-stdout.txt",
        stderr_path=output_root / "summarizer-stderr.txt",
    )
    if external_entries:
        if (
            external_provenance_ledger_path is None
            or external_provenance_ledger_receipt is None
        ):
            raise BenchmarkError(
                "external provenance ledger binding disappeared before sealing"
            )
        verify_external_provenance_ledger_hash(
            external_provenance_ledger_path,
            str(external_provenance_ledger_receipt["sha256"]),
        )

    evidence_files = [
        design_path,
        output_root / "manifest.json",
        output_root / "environment.json",
        output_root / "environment.txt",
        measurement_path,
        summary_path,
        correctness_path,
        comparisons_path,
    ]
    with (output_root / "evidence.sha256").open("w", encoding="utf-8") as handle:
        for path in evidence_files:
            handle.write(f"{sha256_file(path)}  {path.name}\n")

    if external_entries:
        require_no_alignment_artifacts(output_root)
        private_record_streams = sorted(
            path.relative_to(output_root).as_posix()
            for path in output_root.rglob("*.private")
            if path.is_file()
        )
        if private_record_streams:
            raise BenchmarkError(
                "external-input evidence retained private record streams: "
                + ", ".join(private_record_streams[:10])
            )
    scan_public_evidence(output_root, repository_root)
    manifest_path = output_root / "MANIFEST.sha256"
    excluded = {manifest_path.resolve(), (output_root / "STATUS.json").resolve()}
    manifest_entries = sorted(
        path
        for path in output_root.rglob("*")
        if path.is_file() and path.resolve() not in excluded
    )
    temporary_manifest = output_root / "MANIFEST.sha256.tmp"
    with temporary_manifest.open("w", encoding="utf-8") as handle:
        for path in manifest_entries:
            handle.write(f"{sha256_file(path)}  {path.relative_to(output_root).as_posix()}\n")
    temporary_manifest.replace(manifest_path)
    write_status(output_root, "COMPLETE")
    print(f"Benchmark evidence written to {output_root}")
    return 0


def record_failed_run(message: str) -> None:
    """Record failure evidence and independently attempt privacy cleanup."""
    ignore_termination_signals()
    if ACTIVE_OUTPUT_ROOT is None:
        return
    cleanup_errors: list[str] = []
    try:
        write_status(ACTIVE_OUTPUT_ROOT, "FAILED", message)
    except Exception as error:
        cleanup_errors.append(f"status receipt: {error}")
    try:
        summarize_partial_failure(ACTIVE_OUTPUT_ROOT)
    except Exception as error:
        cleanup_errors.append(f"partial summary: {error}")
    if ACTIVE_EXTERNAL_INPUT_MODE:
        try:
            sanitize_external_failure(ACTIVE_OUTPUT_ROOT)
        except Exception as error:
            cleanup_errors.append(f"private artifact cleanup: {error}")
    for error in cleanup_errors:
        print(
            f"warning: failure finalization was incomplete: "
            f"{sanitize_public_text(error)}",
            file=sys.stderr,
        )


def cli_entrypoint(main_function: Callable[[], int] = main) -> int:
    """Run the CLI with signal-aware failure receipts and cleanup."""
    install_termination_signal_handlers()
    try:
        return main_function()
    except BenchmarkError as error:
        record_failed_run(
            (
                "external-input benchmark failed; inspect the path-neutral "
                "contract receipts"
                if ACTIVE_EXTERNAL_INPUT_MODE
                else str(error)
            )
        )
        print(f"error: {error}", file=sys.stderr)
        return 1
    except BenchmarkSignalInterrupt as error:
        ignore_termination_signals()
        signal_name = signal.Signals(error.signal_number).name
        record_failed_run(f"interrupted by {signal_name}")
        print(f"error: interrupted by {signal_name}", file=sys.stderr)
        return 128 + error.signal_number
    except KeyboardInterrupt:
        ignore_termination_signals()
        record_failed_run("interrupted")
        print("error: interrupted", file=sys.stderr)
        return 130
    except Exception as error:
        record_failed_run(
            (
                "external-input benchmark failed unexpectedly; inspect the "
                "path-neutral contract receipts"
                if ACTIVE_EXTERNAL_INPUT_MODE
                else f"unexpected error: {error}"
            )
        )
        raise


if __name__ == "__main__":
    raise SystemExit(cli_entrypoint())
