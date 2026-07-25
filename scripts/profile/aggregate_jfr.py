#!/usr/bin/env python3
"""Create a bounded, path-neutral aggregate of JFR allocation samples."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Counter as CounterType, Dict, List, Mapping, Sequence


SCHEMA_VERSION = 1
AGGREGATOR_VERSION = "1.0.0"
TOP_LIMIT = 20
MAX_RECORDINGS = 32
MAX_RECORDING_BYTES = 16 * 1024 * 1024
MAX_LABEL_CHARACTERS = 512
MAX_EXISTING_OUTPUT_BYTES = 1024 * 1024
READ_CHUNK_BYTES = 1024 * 1024
HIDDEN_CLASS_SUFFIX = re.compile(r"/0x[0-9a-fA-F]+")
SAFE_VERSION = re.compile(r"^[0-9][0-9A-Za-z._+-]*$")
INJECTION_ENVIRONMENT_VARIABLES = (
    "JAVA_TOOL_OPTIONS",
    "_JAVA_OPTIONS",
    "JDK_JAVA_OPTIONS",
    "CLASSPATH",
    "LD_PRELOAD",
    "DYLD_INSERT_LIBRARIES",
)

SENTINEL_DEFINITIONS = (
    {
        "id": "general_clustering_setup",
        "kind": "expected_absent_on_sparse_singleton_path",
        "definition": (
            "Allocated class or sampled stack references Ngram data setup, "
            "Directional.apply, ClusterTracker, or DeduplicateSAM.instantiateData."
        ),
    },
    {
        "id": "singleton_umi_map_promotion",
        "kind": "expected_absent_on_sparse_singleton_path",
        "definition": (
            "A HashMap or ReadFreq allocation is sampled under "
            "DeduplicateSAM.addStreamingRead."
        ),
    },
    {
        "id": "reflective_data_instantiation",
        "kind": "expected_absent_on_sparse_singleton_path",
        "definition": (
            "DeduplicateSAM.instantiateData, or a reflection frame beneath "
            "DeduplicateSAM.flushStreamingGroup, is sampled."
        ),
    },
    {
        "id": "streaming_group_positive_control",
        "kind": "expected_present",
        "definition": (
            "A StreamingAlignReads allocation or constructor frame is sampled."
        ),
    },
    {
        "id": "alignment_key_positive_control",
        "kind": "expected_present",
        "definition": "DeduplicateSAM.singleEndAlignment is sampled.",
    },
)
SENTINEL_IDS = tuple(item["id"] for item in SENTINEL_DEFINITIONS)


class AggregateError(RuntimeError):
    """A recording or aggregation contract was not satisfied."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(READ_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def normalize_class(name: str) -> str:
    """Return a stable dotted class name without runtime hidden-class IDs."""

    return HIDDEN_CLASS_SUFFIX.sub("/0x...", name).replace("/", ".")


def bounded_label(label: str) -> str:
    if len(label) <= MAX_LABEL_CHARACTERS:
        return label

    suffix = hashlib.sha256(label.encode("utf-8")).hexdigest()[:16]
    prefix_length = MAX_LABEL_CHARACTERS - len(suffix) - len("...#")
    return "{}...#{}".format(label[:prefix_length], suffix)


def method_label(frame: Mapping[str, Any]) -> str:
    method = frame["method"]
    return bounded_label(
        "{}.{}".format(normalize_class(method["type"]["name"]), method["name"])
    )


def summarize_counter(
    weights: CounterType[str],
    events: CounterType[str],
    total_weight: int,
) -> List[Dict[str, Any]]:
    result = []
    for label, weight in sorted(
        weights.items(),
        key=lambda item: (-item[1], item[0]),
    )[:TOP_LIMIT]:
        result.append(
            {
                "label": label,
                "event_count": events[label],
                "sample_weight_bytes": weight,
                "sample_weight_share_pct": (
                    round(100.0 * weight / total_weight, 3)
                    if total_weight
                    else 0.0
                ),
            }
        )
    return result


def has_frame(
    frame_pairs: Sequence[tuple[str, str]],
    class_name: str,
    method_name: str,
) -> bool:
    return any(
        observed_class == class_name and observed_method == method_name
        for observed_class, observed_method in frame_pairs
    )


def sentinel_matches(
    identifier: str,
    value: Mapping[str, Any],
    frames: Sequence[Mapping[str, Any]],
) -> bool:
    frame_pairs = [
        (frame["method"]["type"]["name"], frame["method"]["name"])
        for frame in frames
    ]
    allocated_class = value["objectClass"]["name"]

    if identifier == "general_clustering_setup":
        return (
            allocated_class.startswith("umicollapse/data/Ngram")
            or allocated_class == "umicollapse/util/ClusterTracker"
            or any(
                class_name.startswith("umicollapse/data/Ngram")
                or (
                    class_name == "umicollapse/algo/Directional"
                    and method_name == "apply"
                )
                or class_name == "umicollapse/util/ClusterTracker"
                or (
                    class_name == "umicollapse/main/DeduplicateSAM"
                    and method_name == "instantiateData"
                )
                for class_name, method_name in frame_pairs
            )
        )

    if identifier == "singleton_umi_map_promotion":
        under_add_streaming_read = has_frame(
            frame_pairs,
            "umicollapse/main/DeduplicateSAM",
            "addStreamingRead",
        )
        map_or_read_frequency = (
            allocated_class.startswith("java/util/HashMap")
            or allocated_class == "umicollapse/util/ReadFreq"
            or any(
                class_name == "java/util/HashMap"
                for class_name, _ in frame_pairs
            )
        )
        return under_add_streaming_read and map_or_read_frequency

    if identifier == "reflective_data_instantiation":
        instantiate_data = has_frame(
            frame_pairs,
            "umicollapse/main/DeduplicateSAM",
            "instantiateData",
        )
        under_group_flush = has_frame(
            frame_pairs,
            "umicollapse/main/DeduplicateSAM",
            "flushStreamingGroup",
        )
        reflection_frame = any(
            class_name.startswith("java/lang/reflect/")
            or class_name.startswith("jdk/internal/reflect/")
            for class_name, _ in frame_pairs
        )
        return instantiate_data or (under_group_flush and reflection_frame)

    if identifier == "streaming_group_positive_control":
        return (
            allocated_class
            == "umicollapse/main/DeduplicateSAM$StreamingAlignReads"
            or has_frame(
                frame_pairs,
                "umicollapse/main/DeduplicateSAM$StreamingAlignReads",
                "<init>",
            )
        )

    if identifier == "alignment_key_positive_control":
        return has_frame(
            frame_pairs,
            "umicollapse/main/DeduplicateSAM",
            "singleEndAlignment",
        )

    raise AssertionError("unknown sentinel identifier")


def validate_recordings(arguments: argparse.Namespace) -> List[Path]:
    if len(arguments.recordings) > MAX_RECORDINGS:
        raise AggregateError(
            "at most {} recordings may be aggregated".format(MAX_RECORDINGS)
        )

    recordings = [Path(item) for item in arguments.recordings]
    resolved = []
    seen = set()
    output = Path(arguments.output).resolve()

    for index, recording in enumerate(recordings, start=1):
        try:
            candidate = recording.resolve(strict=True)
        except OSError as error:
            raise AggregateError(
                "recording {} is not readable".format(index)
            ) from error
        if not candidate.is_file():
            raise AggregateError(
                "recording {} is not a regular file".format(index)
            )
        if candidate in seen:
            raise AggregateError(
                "recording {} duplicates an earlier input".format(index)
            )
        if candidate == output:
            raise AggregateError("the output must not overwrite a recording")
        size = candidate.stat().st_size
        if size > MAX_RECORDING_BYTES:
            raise AggregateError(
                "recording {} exceeds the {} MiB input limit".format(
                    index,
                    MAX_RECORDING_BYTES // (1024 * 1024),
                )
            )
        seen.add(candidate)
        resolved.append(candidate)

    return resolved


def extract_events(
    jfr_tool: Path,
    recording: Path,
    recording_number: int,
) -> Sequence[Mapping[str, Any]]:
    completed = subprocess.run(
        [
            str(jfr_tool),
            "-J-XX:-UsePerfData",
            "print",
            "--json",
            "--events",
            "jdk.ObjectAllocationSample",
            "--stack-depth",
            "128",
            str(recording),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=jfr_environment(),
    )
    if completed.returncode != 0:
        raise AggregateError(
            "JFR extraction failed for recording {}".format(recording_number)
        )

    try:
        payload = json.loads(completed.stdout)
        events = payload["recording"]["events"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise AggregateError(
            "recording {} produced invalid JFR JSON".format(recording_number)
        ) from error
    if not isinstance(events, list):
        raise AggregateError(
            "recording {} produced an invalid event list".format(recording_number)
        )
    return events


def jfr_environment() -> Dict[str, str]:
    environment = os.environ.copy()
    for variable in INJECTION_ENVIRONMENT_VARIABLES:
        environment.pop(variable, None)
    environment.update({"LANG": "C", "LC_ALL": "C", "TZ": "UTC"})
    return environment


def validate_jfr_tool(jfr_tool: Path) -> str:
    completed = subprocess.run(
        [str(jfr_tool), "-J-XX:-UsePerfData", "version"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=jfr_environment(),
    )
    if completed.returncode != 0:
        raise AggregateError("the JFR tool version check failed")

    lines = completed.stdout.strip().splitlines()
    if len(lines) != 1:
        raise AggregateError("the JFR tool returned an unexpected version")
    version = lines[0].strip()
    if not SAFE_VERSION.fullmatch(version) or version.split(".", 1)[0] != "21":
        raise AggregateError("the aggregator requires the Java 21 JFR tool")
    return version


def validate_output_path(
    path: Path,
    protected_paths: Sequence[Path],
) -> Path:
    resolved = path.resolve()
    if resolved in protected_paths:
        raise AggregateError(
            "the output must not overwrite the aggregator, configuration, "
            "or JFR tool"
        )
    if path.is_symlink():
        raise AggregateError("the output must not be a symbolic link")
    if not path.exists():
        return path
    if not path.is_file():
        raise AggregateError("an existing output must be a regular file")
    if any(os.path.samefile(path, protected) for protected in protected_paths):
        raise AggregateError(
            "the output must not overwrite the aggregator, configuration, "
            "or JFR tool"
        )
    if path.stat().st_size > MAX_EXISTING_OUTPUT_BYTES:
        raise AggregateError("refusing to replace an unrecognized existing output")

    try:
        prior = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AggregateError(
            "refusing to replace an unrecognized existing output"
        ) from error
    if (
        not isinstance(prior, dict)
        or prior.get("schema") != "dumi-allocation-profile"
        or prior.get("schema_version") != SCHEMA_VERSION
    ):
        raise AggregateError("refusing to replace an unrecognized existing output")
    return path


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=".{}.".format(path.name),
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(
            file_descriptor,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate JFR ObjectAllocationSample recordings without retaining "
            "paths, timestamps, threads, or individual events."
        )
    )
    parser.add_argument(
        "--jfr-tool",
        required=True,
        help="Java 21 jfr executable",
    )
    parser.add_argument(
        "--configuration",
        default=str(Path(__file__).with_name("allocation-only.jfc")),
        help="allocation-only.jfc used for the recordings",
    )
    parser.add_argument("--output", required=True, help="aggregate JSON destination")
    parser.add_argument("recordings", nargs="+", help="one to 32 JFR recordings")
    return parser


def main() -> int:
    parser = build_parser()
    arguments = parser.parse_args()

    try:
        recordings = validate_recordings(arguments)
        jfr_tool = Path(arguments.jfr_tool).resolve(strict=True)
        if not jfr_tool.is_file():
            raise AggregateError("--jfr-tool is not a regular file")
        jfr_tool_version = validate_jfr_tool(jfr_tool)

        configured = Path(arguments.configuration).resolve(strict=True)
        shipped = Path(__file__).with_name("allocation-only.jfc").resolve(strict=True)
        if configured.read_bytes() != shipped.read_bytes():
            raise AggregateError(
                "--configuration must match the shipped allocation-only.jfc"
            )
        protected_outputs = {
            jfr_tool,
            configured,
            shipped,
            Path(__file__).resolve(),
        }
        output = validate_output_path(
            Path(arguments.output),
            tuple(protected_outputs),
        )

        pooled_class_weights: CounterType[str] = Counter()
        pooled_class_events: CounterType[str] = Counter()
        pooled_site_weights: CounterType[str] = Counter()
        pooled_site_events: CounterType[str] = Counter()
        pooled_dumi_weights: CounterType[str] = Counter()
        pooled_dumi_events: CounterType[str] = Counter()
        pooled_sentinel_weights: CounterType[str] = Counter()
        pooled_sentinel_events: CounterType[str] = Counter()
        runs: List[Dict[str, Any]] = []
        pooled_total = 0
        pooled_event_count = 0

        for run_number, recording in enumerate(recordings, start=1):
            events = extract_events(jfr_tool, recording, run_number)
            run_total = 0
            run_sentinel_weights: CounterType[str] = Counter()
            run_sentinel_events: CounterType[str] = Counter()

            for event in events:
                if event.get("type") != "jdk.ObjectAllocationSample":
                    raise AggregateError(
                        "recording {} contains an unexpected event".format(run_number)
                    )
                value = event.get("values")
                if not isinstance(value, dict):
                    raise AggregateError(
                        "recording {} contains invalid event values".format(run_number)
                    )
                weight = value.get("weight")
                if not isinstance(weight, int) or weight < 0:
                    raise AggregateError(
                        "recording {} contains an invalid sample weight".format(
                            run_number
                        )
                    )
                object_class = value.get("objectClass")
                if not isinstance(object_class, dict) or not isinstance(
                    object_class.get("name"),
                    str,
                ):
                    raise AggregateError(
                        "recording {} contains an invalid object class".format(
                            run_number
                        )
                    )
                stack_trace = value.get("stackTrace")
                if stack_trace is not None and not isinstance(stack_trace, dict):
                    raise AggregateError(
                        "recording {} contains an invalid stack trace".format(
                            run_number
                        )
                    )
                frames = [] if stack_trace is None else stack_trace.get("frames") or []
                if not isinstance(frames, list):
                    raise AggregateError(
                        "recording {} contains an invalid stack trace".format(
                            run_number
                        )
                    )

                allocation_class = bounded_label(normalize_class(object_class["name"]))
                site = method_label(frames[0]) if frames else "unattributed"
                dumi_site = next(
                    (
                        method_label(frame)
                        for frame in frames
                        if frame["method"]["type"]["name"].startswith(
                            "umicollapse/"
                        )
                    ),
                    "no_dumi_frame",
                )

                pooled_class_weights[allocation_class] += weight
                pooled_class_events[allocation_class] += 1
                pooled_site_weights[site] += weight
                pooled_site_events[site] += 1
                pooled_dumi_weights[dumi_site] += weight
                pooled_dumi_events[dumi_site] += 1
                run_total += weight

                for identifier in SENTINEL_IDS:
                    if sentinel_matches(identifier, value, frames):
                        run_sentinel_weights[identifier] += weight
                        run_sentinel_events[identifier] += 1

            if not events or run_total <= 0:
                raise AggregateError(
                    "recording {} contains no weighted allocation samples".format(
                        run_number
                    )
                )

            for identifier in SENTINEL_IDS:
                pooled_sentinel_weights[identifier] += run_sentinel_weights[
                    identifier
                ]
                pooled_sentinel_events[identifier] += run_sentinel_events[
                    identifier
                ]

            runs.append(
                {
                    "run": run_number,
                    "recording_bytes": recording.stat().st_size,
                    "recording_sha256": sha256_file(recording),
                    "event_count": len(events),
                    "sample_weight_bytes": run_total,
                    "sentinels": [
                        {
                            "id": identifier,
                            "event_count": run_sentinel_events[identifier],
                            "sample_weight_bytes": run_sentinel_weights[identifier],
                        }
                        for identifier in SENTINEL_IDS
                    ],
                }
            )
            pooled_total += run_total
            pooled_event_count += len(events)

        aggregate = {
            "schema": "dumi-allocation-profile",
            "schema_version": SCHEMA_VERSION,
            "aggregator": {
                "version": AGGREGATOR_VERSION,
                "source": "scripts/profile/aggregate_jfr.py",
                "source_sha256": sha256_file(Path(__file__).resolve()),
            },
            "configuration": {
                "source": "scripts/profile/allocation-only.jfc",
                "source_sha256": sha256_file(configured),
                "event": "jdk.ObjectAllocationSample",
                "throttle": "1000/s",
                "stack_depth": 128,
                "jfr_tool_version": jfr_tool_version,
            },
            "interpretation": (
                "Aggregated JFR sample weights estimate allocation pressure; "
                "they are not exact allocated bytes, retained heap, or object counts."
            ),
            "limits": {
                "maximum_recordings": MAX_RECORDINGS,
                "maximum_recording_bytes": MAX_RECORDING_BYTES,
                "maximum_label_characters": MAX_LABEL_CHARACTERS,
                "maximum_existing_output_bytes": MAX_EXISTING_OUTPUT_BYTES,
                "top_entries_per_dimension": TOP_LIMIT,
            },
            "runs": runs,
            "aggregate": {
                "event_count": pooled_event_count,
                "sample_weight_bytes": pooled_total,
                "top_allocated_classes": summarize_counter(
                    pooled_class_weights,
                    pooled_class_events,
                    pooled_total,
                ),
                "top_allocation_sites": summarize_counter(
                    pooled_site_weights,
                    pooled_site_events,
                    pooled_total,
                ),
                "top_dumi_ancestor_sites": summarize_counter(
                    pooled_dumi_weights,
                    pooled_dumi_events,
                    pooled_total,
                ),
                "sentinels": [
                    {
                        **definition,
                        "event_count": pooled_sentinel_events[definition["id"]],
                        "sample_weight_bytes": pooled_sentinel_weights[
                            definition["id"]
                        ],
                        "per_run_event_count": [
                            run["sentinels"][index]["event_count"] for run in runs
                        ],
                        "per_run_sample_weight_bytes": [
                            run["sentinels"][index]["sample_weight_bytes"]
                            for run in runs
                        ],
                    }
                    for index, definition in enumerate(SENTINEL_DEFINITIONS)
                ],
            },
        }
        atomic_json(output, aggregate)
        return 0
    except (AggregateError, OSError, subprocess.SubprocessError) as error:
        parser.error(str(error))

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
