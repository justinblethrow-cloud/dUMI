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
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


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
PUBLIC_PATH_REPLACEMENTS: list[tuple[str, str]] = []

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
    "command_file",
    "stdout_file",
    "stderr_file",
    "output_file",
]


class BenchmarkError(RuntimeError):
    """A benchmark contract or external command failed."""


@dataclass(frozen=True)
class Workload:
    name: str
    scale: str
    umi_length: int
    paired: bool
    generator_args: tuple[str, ...]


@dataclass(frozen=True)
class Implementation:
    name: str
    mode: str
    source_key: str

    @property
    def label(self) -> str:
        return self.name if self.mode == "legacy" else f"{self.name}-{self.mode}"


def implementations_for(
    workload: Workload, include_intermediate: bool
) -> list[Implementation]:
    if workload.paired:
        implementations = [
            Implementation("canonical-upstream", "legacy", "upstream"),
        ]
        if include_intermediate:
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
    return [
        Implementation("canonical-upstream", "legacy", "upstream"),
        Implementation("dumi", "off", "dumi"),
        Implementation("dumi", "on", "dumi"),
        Implementation("dumi", "auto", "dumi"),
    ]


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
    try:
        completed = subprocess.run(
            command_strings,
            cwd=cwd,
            stdout=stdout_handle if stdout_handle else subprocess.PIPE,
            stderr=stderr_handle if stderr_handle else subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
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
        ".sam",
    }
    private_roots = (
        "/" + "mnt" + "/",
        "/" + "home" + "/",
        "/" + "Users" + "/",
    )
    organization_name = "plasmid" + "saurus"
    exact_private_values = {
        private
        for private, replacement in PUBLIC_PATH_REPLACEMENTS
        if replacement in {"<EVIDENCE_DIR>", "<DUMI_REPOSITORY>", "<HOME>"}
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
        if organization_name in text.lower():
            findings.append(f"{relative}: organization-specific branding")
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
                    "no organization-specific branding",
                    "no local user or host tokens in generated textual evidence",
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
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
) -> dict[str, object]:
    temporary_root.mkdir(parents=True, exist_ok=True)
    command = [
        python,
        checker,
        "--samtools",
        samtools,
        "--tmpdir",
        temporary_root,
        output,
    ]
    completed = run_command(command)
    try:
        result = json.loads(completed.stdout or "")
    except json.JSONDecodeError as exc:
        raise BenchmarkError(f"semantic checker returned invalid JSON for {output}") from exc
    if result.get("quickcheck_status") != "pass":
        raise BenchmarkError(f"samtools quickcheck failed for {output}")
    if ACTIVE_OUTPUT_ROOT is not None:
        result["output_file"] = record_path(output, ACTIVE_OUTPUT_ROOT)
        if result.get("reference_file"):
            result["reference_file"] = sanitize_public_text(
                str(result["reference_file"])
            )
    result["output_bytes"] = output.stat().st_size
    result["output_sha256"] = sha256_file(output)
    return result


def validate_streaming_contract(
    *,
    stdout_path: Path,
    stderr_path: Path,
    sort_order: object,
    should_stream: bool,
    context: str,
) -> None:
    stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
    stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
    marker_seen = STREAMING_MARKER in stdout_text
    fallback_seen = STREAMING_FALLBACK_MARKER in stderr_text
    expected_sort_order = "unsorted" if should_stream else "coordinate"

    if marker_seen != should_stream:
        raise BenchmarkError(f"unexpected streaming selection in {context}")
    if fallback_seen:
        raise BenchmarkError(f"streaming fallback occurred in {context}")
    if str(sort_order) != expected_sort_order:
        raise BenchmarkError(
            f"unexpected raw sort order in {context}: observed {sort_order!r}, "
            f"expected {expected_sort_order!r}"
        )


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run source-normalized canonical UMICollapse versus dUMI benchmarks."
    )
    parser.add_argument("--output-dir", help="new/empty evidence directory (default: /tmp)")
    parser.add_argument("--dumi-ref", default="HEAD", help="committed dUMI Git ref to benchmark")
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument(
        "--workloads",
        default="sparse,moderate,hotspot,paired",
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
    parser.add_argument("--active-processors", type=int, default=8)
    parser.add_argument("--xms", default="64m")
    parser.add_argument("--xmx", default="4g")
    parser.add_argument("--seed", type=int, default=1729)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    global ACTIVE_OUTPUT_ROOT, PUBLIC_PATH_REPLACEMENTS
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
    if args.moderate_groups <= 0 or args.hotspot_families <= 0:
        raise BenchmarkError("workload sizes must be positive")
    if args.paired_pairs_per_reference <= 0 or args.moderate_families_per_group <= 0:
        raise BenchmarkError("workload multiplicities must be positive")

    script_root = Path(__file__).resolve().parent
    repository_root = script_root.parents[1]
    python = Path(sys.executable).resolve()
    generator = script_root / "generate_workload.py"
    checker = script_root / "semantic_check.py"
    summarizer = script_root / "summarize_results.py"
    benchmark_readme = script_root / "README.md"
    harness_sources = (
        Path(__file__).resolve(),
        generator,
        checker,
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
    if is_within(output_root, repository_root) and not args.allow_output_in_repo:
        raise BenchmarkError(
            "refusing to place generated benchmark data inside the repository; "
            "choose an external --output-dir or pass --allow-output-in-repo"
        )
    ensure_empty_output(output_root)
    ACTIVE_OUTPUT_ROOT = output_root
    process_tmp = output_root / "process-tmp"
    process_tmp.mkdir()
    process_home = output_root / "process-home"
    process_home.mkdir()
    os.environ["TMPDIR"] = os.fspath(process_tmp)
    PUBLIC_PATH_REPLACEMENTS = [
        (os.fspath(output_root), "<EVIDENCE_DIR>"),
        (os.fspath(repository_root), "<DUMI_REPOSITORY>"),
        (os.fspath(original_home), "<HOME>"),
    ]
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
    summarizer = harness_snapshot_root / summarizer.name

    git = require_tool("git")
    curl = require_tool("curl")
    samtools = require_tool("samtools", args.samtools)
    gnu_time = find_gnu_time(args.gnu_time)
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

    selected = [item.strip() for item in args.workloads.split(",") if item.strip()]
    allowed = {"sparse", "moderate", "hotspot", "paired"}
    if not selected or set(selected) - allowed:
        raise BenchmarkError(f"--workloads must be a subset of {sorted(allowed)}")
    if len(selected) != len(set(selected)):
        raise BenchmarkError("--workloads must not contain duplicate values")

    sparse_records = parse_positive_list(args.sparse_records, "--sparse-records")
    paired_references = parse_positive_list(args.paired_references, "--paired-references")
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

    dumi_sha = capture([git, "-C", repository_root, "rev-parse", f"{args.dumi_ref}^{{commit}}"])
    if not git_has_commit(git, repository_root, dumi_sha):
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
    if args.include_intermediate and "paired" in selected:
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

    environment_commands = [
        ("uname", ["uname", "-srmo"]),
        ("java", [java, "-version"]),
        ("javac", [javac, "-version"]),
        ("samtools", [samtools, "--version"]),
        ("gnu_time", [gnu_time, "--version"]),
        ("git", [git, "--version"]),
        ("python", [python, "--version"]),
    ]
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
        "format": 1,
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
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
            "ref": args.dumi_ref,
            "sha": dumi_sha,
            "uncommitted_worktree_sources_excluded": True,
            "worktree_was_dirty": bool(worktree_status),
        },
        "dependencies": dependencies,
        "dependency_files": [
            {"path": record_path(path, output_root), "sha256": sha256_file(path)}
            for path in dependency_paths
        ],
        "harness_files": harness_files,
        "builds": builds,
        "config": {
            "active_processors": args.active_processors,
            "allow_output_in_repo": args.allow_output_in_repo,
            "dumi_ref": args.dumi_ref,
            "hotspot_families": args.hotspot_families,
            "include_intermediate": args.include_intermediate,
            "keep_outputs": args.keep_outputs,
            "moderate_families_per_group": args.moderate_families_per_group,
            "moderate_groups": args.moderate_groups,
            "paired_pairs_per_reference": args.paired_pairs_per_reference,
            "paired_references": paired_references,
            "profile": args.profile,
            "repetitions": args.repetitions,
            "seed": args.seed,
            "selected_workloads": selected,
            "sparse_records": sparse_records,
            "xms": args.xms,
            "xmx": args.xmx,
        },
        "subprocess_environment": environment_json["subprocess_environment"],
        "jvm_options": [
            "-XX:-UsePerfData",
            "-server",
            f"-Xms{args.xms}",
            f"-Xmx{args.xmx}",
            "-Xss20m",
            f"-XX:ActiveProcessorCount={args.active_processors}",
        ],
    }
    runtime_identity = {
        "java": environment_json["java"],
        "javac": environment_json["javac"],
        "dependencies": [
            {"filename": item["filename"], "sha256": item["sha256"]}
            for item in dependencies
        ],
        "jvm_options": manifest["jvm_options"],
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
            "paired": workload.paired,
            "generator_arguments": list(workload.generator_args),
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
            for repetition in range(1, args.repetitions + 1):
                ordered = latin_order(
                    implementations, repetition - 1 + workload_index
                )
                for order_index, implementation in enumerate(ordered, 1):
                    logical_id = (
                        f"{workload.name}-{workload.scale}-r{repetition:02d}-"
                        f"o{order_index:02d}-{implementation.label}"
                    )
                    for stage in ("raw", "ready"):
                        design_writer.writerow(
                            {
                                "run_id": f"{logical_id}-{stage}",
                                "workload": workload.name,
                                "scale": workload.scale,
                                "stage": stage,
                                "implementation": implementation.name,
                                "mode": implementation.mode,
                                "repetition": repetition,
                                "order": order_index,
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

    jvm_options = [
        "-XX:-UsePerfData",
        "-server",
        f"-Xms{args.xms}",
        f"-Xmx{args.xmx}",
        "-Xss20m",
        f"-XX:ActiveProcessorCount={args.active_processors}",
    ]

    try:
        for workload_index, workload in enumerate(workloads):
            input_root = output_root / "inputs" / workload.name / workload.scale
            input_root.mkdir(parents=True, exist_ok=True)
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
                workload_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                expected_output_records = int(
                    workload_metadata["expected_output"]["records"]
                )
                expected_output_sha256 = str(
                    workload_metadata["expected_output"]["canonical_record_sha256"]
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
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise BenchmarkError(
                    f"generator emitted malformed metadata for {workload.name}/{workload.scale}"
                ) from error
            if (
                workload_metadata["input"]["sha256"] != sha256_file(sam_input)
                or generated_bytes != sam_input.stat().st_size
                or generated_umi_length != workload.umi_length
                or len(expected_output_sha256) != 64
                or len(expected_reference_dictionary_sha256) != 64
            ):
                raise BenchmarkError(
                    f"generator receipt mismatch for {workload.name}/{workload.scale}"
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
                    "path": record_path(Path(str(bam_input) + ".bai"), output_root),
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
                java_command = [
                    java,
                    *jvm_options,
                    f"-Djava.io.tmpdir={java_tmp}",
                    "-cp",
                    os.pathsep.join(
                        [os.fspath(classes[implementation.source_key]), common_classpath]
                    ),
                    "umicollapse.main.Main",
                    "bam",
                    "-i",
                    bam_input,
                    "-o",
                    warm_output,
                    "-u",
                    str(workload.umi_length),
                    "--algo",
                    "dir",
                    "--data",
                    "ngrambktree",
                    "--merge",
                    "mapqual",
                ]
                if workload.paired:
                    java_command.append("--paired")
                if implementation.name == "dumi":
                    java_command.extend(["--streaming-mode", implementation.mode])
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
                warm_inspection = inspect_output(
                    checker=checker,
                    python=python,
                    samtools=samtools,
                    output=warm_output,
                    temporary_root=warm_root / "semantic-tmp",
                )
                should_stream = not workload.paired and implementation.name == "dumi" and (
                    implementation.mode in {"on", "auto"}
                )
                validate_streaming_contract(
                    stdout_path=warm_root / "stdout.txt",
                    stderr_path=warm_root / "stderr.txt",
                    sort_order=warm_inspection["sort_order"],
                    should_stream=should_stream,
                    context=(
                        f"warm-up for {workload.name}/{workload.scale}/"
                        f"{implementation.label}"
                    ),
                )
                if (
                    int(warm_inspection["output_records"]) != expected_output_records
                    or str(warm_inspection["semantic_sha256"])
                    != expected_output_sha256
                    or int(warm_inspection["reference_sequences"])
                    != expected_reference_sequences
                    or str(warm_inspection["reference_dictionary_sha256"])
                    != expected_reference_dictionary_sha256
                ):
                    raise BenchmarkError(
                        f"warm-up output does not match the generator oracle for "
                        f"{workload.name}/{workload.scale}/{implementation.label}"
                    )
                if not args.keep_outputs:
                    warm_output.unlink(missing_ok=True)

            contract_root = output_root / "contracts" / workload.name / workload.scale
            contract_root.mkdir(parents=True, exist_ok=True)
            default_output = contract_root / "default-auto.bam"
            default_tmp = contract_root / "java-tmp"
            default_tmp.mkdir()
            default_command = [
                java,
                *jvm_options,
                f"-Djava.io.tmpdir={default_tmp}",
                "-cp",
                os.pathsep.join([os.fspath(classes["dumi"]), common_classpath]),
                "umicollapse.main.Main",
                "bam",
                "-i",
                bam_input,
                "-o",
                default_output,
                "-u",
                str(workload.umi_length),
                "--algo",
                "dir",
                "--data",
                "ngrambktree",
                "--merge",
                "mapqual",
            ]
            if workload.paired:
                default_command.append("--paired")
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
            )
            (contract_root / "default-inspection.json").write_text(
                json.dumps(default_inspection, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            validate_streaming_contract(
                stdout_path=contract_root / "default-stdout.txt",
                stderr_path=contract_root / "default-stderr.txt",
                sort_order=default_inspection["sort_order"],
                should_stream=not workload.paired,
                context=f"default/no-flag contract for {workload.name}/{workload.scale}",
            )
            if (
                int(default_inspection["output_records"]) != expected_output_records
                or str(default_inspection["semantic_sha256"])
                != expected_output_sha256
                or int(default_inspection["reference_sequences"])
                != expected_reference_sequences
                or str(default_inspection["reference_dictionary_sha256"])
                != expected_reference_dictionary_sha256
            ):
                raise BenchmarkError(
                    f"default/no-flag output does not match the generator oracle for "
                    f"{workload.name}/{workload.scale}"
                )
            if workload.paired:
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

            group_results: list[dict[str, object]] = []
            for repetition in range(1, args.repetitions + 1):
                order = latin_order(
                    implementations, repetition - 1 + workload_index
                )
                for order_index, implementation in enumerate(order, 1):
                    run_id = (
                        f"{workload.name}-{workload.scale}-r{repetition:02d}-"
                        f"o{order_index:02d}-{implementation.label}"
                    )
                    run_root = output_root / "runs" / run_id
                    raw_root = run_root / "raw"
                    raw_output = raw_root / "output.bam"
                    java_tmp = raw_root / "java-tmp"
                    java_tmp.mkdir(parents=True, exist_ok=True)
                    java_command = [
                        java,
                        *jvm_options,
                        f"-Djava.io.tmpdir={java_tmp}",
                        "-cp",
                        os.pathsep.join(
                            [os.fspath(classes[implementation.source_key]), common_classpath]
                        ),
                        "umicollapse.main.Main",
                        "bam",
                        "-i",
                        bam_input,
                        "-o",
                        raw_output,
                        "-u",
                        str(workload.umi_length),
                        "--algo",
                        "dir",
                        "--data",
                        "ngrambktree",
                        "--merge",
                        "mapqual",
                    ]
                    if workload.paired:
                        java_command.append("--paired")
                    if implementation.name == "dumi":
                        java_command.extend(["--streaming-mode", implementation.mode])

                    run_command(
                        [samtools, "view", "-c", bam_input],
                        stdout_path=raw_root / "preread-stdout.txt",
                        stderr_path=raw_root / "preread-stderr.txt",
                    )
                    exit_code, metrics = timed_command(
                        command=java_command,
                        run_root=raw_root,
                        gnu_time=gnu_time,
                    )
                    if exit_code != 0:
                        sanitize_text_file(raw_root / "stdout.txt")
                        sanitize_text_file(raw_root / "stderr.txt")
                        raise BenchmarkError(
                            f"timed command failed; evidence retained in {raw_root}"
                        )
                    should_stream = not workload.paired and implementation.name == "dumi" and (
                        implementation.mode in {"on", "auto"}
                    )

                    ready_root = run_root / "ready"
                    ready_root.mkdir(parents=True, exist_ok=True)
                    if should_stream:
                        ready_output = ready_root / "output.coordinate.bam"
                        shell_command = (
                            f"{shlex.quote(os.fspath(samtools))} sort -o "
                            f"{shlex.quote(os.fspath(ready_output))} "
                            f"{shlex.quote(os.fspath(raw_output))} && "
                            f"{shlex.quote(os.fspath(samtools))} index "
                            f"{shlex.quote(os.fspath(ready_output))}"
                        )
                        ready_command = ["bash", "-c", shell_command]
                    else:
                        ready_output = raw_output
                        ready_command = [
                            samtools,
                            "index",
                            raw_output,
                        ]
                    ready_exit, ready_metrics = timed_command(
                        command=ready_command,
                        run_root=ready_root,
                        gnu_time=gnu_time,
                    )
                    sanitize_text_file(raw_root / "stdout.txt")
                    sanitize_text_file(raw_root / "stderr.txt")
                    sanitize_text_file(ready_root / "stdout.txt")
                    sanitize_text_file(ready_root / "stderr.txt")
                    if ready_exit != 0:
                        raise BenchmarkError(
                            f"downstream-ready command failed; evidence retained in {ready_root}"
                        )

                    raw_inspection = inspect_output(
                        checker=checker,
                        python=python,
                        samtools=samtools,
                        output=raw_output,
                        temporary_root=raw_root / "semantic-tmp",
                    )
                    if (
                        int(raw_inspection["output_records"]) != expected_output_records
                        or str(raw_inspection["semantic_sha256"])
                        != expected_output_sha256
                        or int(raw_inspection["reference_sequences"])
                        != expected_reference_sequences
                        or str(raw_inspection["reference_dictionary_sha256"])
                        != expected_reference_dictionary_sha256
                    ):
                        raise BenchmarkError(
                            f"output does not match the generator oracle in {run_id}"
                        )
                    validate_streaming_contract(
                        stdout_path=raw_root / "stdout.txt",
                        stderr_path=raw_root / "stderr.txt",
                        sort_order=raw_inspection["sort_order"],
                        should_stream=should_stream,
                        context=run_id,
                    )

                    elapsed, user_s, system_s, cpu_pct, rss, timed_exit = metrics
                    row = {
                        "run_id": f"{run_id}-raw",
                        "workload": workload.name,
                        "scale": workload.scale,
                        "stage": "raw",
                        "implementation": implementation.name,
                        "mode": implementation.mode,
                        "repetition": repetition,
                        "order": order_index,
                        "exit_code": timed_exit or exit_code,
                        "elapsed_s": elapsed,
                        "user_s": user_s,
                        "system_s": system_s,
                        "cpu_pct": cpu_pct,
                        "max_rss_kib": rss,
                        "input_sha256": input_hash,
                        "output_records": raw_inspection["output_records"],
                        "semantic_sha256": raw_inspection["semantic_sha256"],
                        "sort_order": raw_inspection["sort_order"],
                        "output_bytes": raw_inspection["output_bytes"],
                        "output_sha256": raw_inspection["output_sha256"],
                        "reference_sequences": raw_inspection["reference_sequences"],
                        "reference_dictionary_sha256": (
                            raw_inspection["reference_dictionary_sha256"]
                        ),
                        "expected_output_records": expected_output_records,
                        "expected_semantic_sha256": expected_output_sha256,
                        "expected_reference_sequences": expected_reference_sequences,
                        "expected_reference_dictionary_sha256": (
                            expected_reference_dictionary_sha256
                        ),
                        "command_file": record_path(raw_root / "command.txt", output_root),
                        "stdout_file": record_path(raw_root / "stdout.txt", output_root),
                        "stderr_file": record_path(raw_root / "stderr.txt", output_root),
                        "output_file": (
                            record_path(raw_output, output_root)
                            if args.keep_outputs
                            else ""
                        ),
                    }
                    writer.writerow(row)
                    measurement_handle.flush()
                    group_results.append(row)

                    ready_inspection = inspect_output(
                        checker=checker,
                        python=python,
                        samtools=samtools,
                        output=ready_output,
                        temporary_root=ready_root / "semantic-tmp",
                    )
                    if (
                        ready_inspection["semantic_sha256"]
                        != raw_inspection["semantic_sha256"]
                        or ready_inspection["output_records"]
                        != raw_inspection["output_records"]
                        or ready_inspection["reference_sequences"]
                        != raw_inspection["reference_sequences"]
                        or ready_inspection["reference_dictionary_sha256"]
                        != raw_inspection["reference_dictionary_sha256"]
                        or ready_inspection["sort_order"] != "coordinate"
                    ):
                        raise BenchmarkError(f"downstream-ready output mismatch in {run_id}")

                    elapsed, user_s, system_s, cpu_pct, rss, timed_exit = ready_metrics
                    ready_row = {
                        "run_id": f"{run_id}-ready",
                        "workload": workload.name,
                        "scale": workload.scale,
                        "stage": "ready",
                        "implementation": implementation.name,
                        "mode": implementation.mode,
                        "repetition": repetition,
                        "order": order_index,
                        "exit_code": timed_exit or ready_exit,
                        "elapsed_s": elapsed,
                        "user_s": user_s,
                        "system_s": system_s,
                        "cpu_pct": cpu_pct,
                        "max_rss_kib": rss,
                        "input_sha256": input_hash,
                        "output_records": ready_inspection["output_records"],
                        "semantic_sha256": ready_inspection["semantic_sha256"],
                        "sort_order": ready_inspection["sort_order"],
                        "output_bytes": ready_inspection["output_bytes"],
                        "output_sha256": ready_inspection["output_sha256"],
                        "reference_sequences": ready_inspection["reference_sequences"],
                        "reference_dictionary_sha256": (
                            ready_inspection["reference_dictionary_sha256"]
                        ),
                        "expected_output_records": expected_output_records,
                        "expected_semantic_sha256": expected_output_sha256,
                        "expected_reference_sequences": expected_reference_sequences,
                        "expected_reference_dictionary_sha256": (
                            expected_reference_dictionary_sha256
                        ),
                        "command_file": record_path(ready_root / "command.txt", output_root),
                        "stdout_file": record_path(ready_root / "stdout.txt", output_root),
                        "stderr_file": record_path(ready_root / "stderr.txt", output_root),
                        "output_file": (
                            record_path(ready_output, output_root)
                            if args.keep_outputs
                            else ""
                        ),
                    }
                    writer.writerow(ready_row)
                    measurement_handle.flush()

                    if not args.keep_outputs:
                        for candidate in {
                            raw_output,
                            ready_output,
                            Path(str(raw_output) + ".bai"),
                            Path(str(ready_output) + ".bai"),
                            Path(str(ready_output) + ".csi"),
                        }:
                            candidate.unlink(missing_ok=True)

            semantic_hashes = {str(row["semantic_sha256"]) for row in group_results}
            record_counts = {str(row["output_records"]) for row in group_results}
            reference_hashes = {
                str(row["reference_dictionary_sha256"]) for row in group_results
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
            if str(default_inspection["semantic_sha256"]) not in semantic_hashes:
                raise BenchmarkError(
                    f"default/no-flag output mismatch for {workload.name}/{workload.scale}"
                )
            if not args.keep_outputs:
                default_output.unlink(missing_ok=True)
    finally:
        measurement_handle.close()

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


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BenchmarkError as error:
        if ACTIVE_OUTPUT_ROOT is not None:
            write_status(ACTIVE_OUTPUT_ROOT, "FAILED", str(error))
            summarize_partial_failure(ACTIVE_OUTPUT_ROOT)
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        if ACTIVE_OUTPUT_ROOT is not None:
            write_status(ACTIVE_OUTPUT_ROOT, "FAILED", "interrupted")
            summarize_partial_failure(ACTIVE_OUTPUT_ROOT)
        print("error: interrupted", file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        if ACTIVE_OUTPUT_ROOT is not None:
            write_status(ACTIVE_OUTPUT_ROOT, "FAILED", f"unexpected error: {error}")
            summarize_partial_failure(ACTIVE_OUTPUT_ROOT)
        raise
