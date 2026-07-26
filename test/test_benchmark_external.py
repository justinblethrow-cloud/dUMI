#!/usr/bin/env python3
"""Focused tests for the privacy-hardened external-BAM benchmark mode."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "scripts" / "benchmark" / "run_benchmark.py"
CHECKER_PATH = ROOT / "scripts" / "benchmark" / "semantic_check.py"

SPEC = importlib.util.spec_from_file_location("dumi_benchmark_runner", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUNNER
SPEC.loader.exec_module(RUNNER)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ExternalBenchmarkTest(unittest.TestCase):
    def setUp(self) -> None:
        samtools = shutil.which("samtools")
        self.samtools = Path(samtools).resolve() if samtools else None
        self.temporary = tempfile.TemporaryDirectory(
            prefix="dumi-external-benchmark-test."
        )
        self.root = Path(self.temporary.name)
        self.bam = self.root / "private-input.bam"
        if self.samtools is not None:
            subprocess.run(
                [
                    self.samtools,
                    "view",
                    "-b",
                    "-o",
                    self.bam,
                    ROOT / "test" / "fixtures" / "streaming-core.sam",
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        else:
            self.bam.write_bytes(b"manifest parser fixture, not a BAM\n")
        RUNNER.PUBLIC_PATH_REPLACEMENTS = []

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_manifest(self, *, workload_id: str = "demo-small") -> Path:
        manifest = self.root / "inputs.tsv"
        manifest.write_text(
            "\t".join(
                (
                    "workload_id",
                    "bam_path",
                    "bam_sha256",
                    "paired",
                    "umi_length",
                    "umi_separator",
                    "rationale",
                )
            )
            + "\n"
            + "\t".join(
                (
                    workload_id,
                    self.bam.name,
                    sha256(self.bam),
                    "false",
                    "4",
                    "_",
                    "small public-safe smoke workload",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        return manifest

    def test_tsv_manifest_and_bam_validation_are_path_free(self) -> None:
        if self.samtools is None:
            self.skipTest("samtools is required for BAM validation")
        validation_root = self.root / "evidence" / "demo-small"
        RUNNER.PUBLIC_PATH_REPLACEMENTS = [
            (str(self.root / "evidence"), "<EVIDENCE_DIR>")
        ]
        entries = RUNNER.parse_external_manifest(
            str(self.write_manifest())
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].bam_path, self.bam.resolve())
        receipt = RUNNER.validate_external_bam(
            entry=entries[0],
            samtools=self.samtools,
            validation_root=validation_root,
        )
        self.assertEqual(receipt["quickcheck_status"], "pass")
        self.assertEqual(receipt["temporary_index_validation"], "pass")
        self.assertEqual(receipt["qnames_checked"], 11)
        self.assertFalse(receipt["path_recorded"])
        evidence_text = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in validation_root.rglob("*")
            if path.is_file()
        )
        self.assertNotIn(str(self.root), evidence_text)
        self.assertNotIn(self.bam.name, evidence_text)
        self.assertIn("<EXTERNAL_BAM:demo-small>", evidence_text)
        self.assertFalse((validation_root / "temporary-input-index.bai").exists())

    def test_manifest_rejects_invalid_slug_and_duplicate_content(self) -> None:
        with self.assertRaisesRegex(
            RUNNER.BenchmarkError, "neutral lowercase slug"
        ):
            RUNNER.parse_external_manifest(
                str(self.write_manifest(workload_id="PRIVATE01"))
            )

        second = self.root / "same-content.bam"
        shutil.copyfile(self.bam, second)
        manifest = self.root / "duplicates.json"
        manifest.write_text(
            json.dumps(
                {
                    "format": 1,
                    "workloads": [
                        {
                            "workload_id": workload_id,
                            "bam_path": path.name,
                            "bam_sha256": sha256(path),
                            "paired": False,
                            "umi_length": 4,
                            "umi_separator": "_",
                        }
                        for workload_id, path in (
                            ("demo-a", self.bam),
                            ("demo-b", second),
                        )
                    ],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            RUNNER.BenchmarkError, "content hash is used"
        ):
            RUNNER.parse_external_manifest(str(manifest))

    def test_manifest_rejects_non_ascii_umi_separator_early(self) -> None:
        manifest = self.write_manifest()
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace(
                "\t_\t", "\té\t"
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            RUNNER.BenchmarkError,
            "umi_separator must contain only ASCII",
        ):
            RUNNER.parse_external_manifest(str(manifest))

    def test_external_provenance_ledger_is_required_and_hash_bound(
        self,
    ) -> None:
        manifest = self.write_manifest()
        entries = RUNNER.parse_external_manifest(str(manifest))
        with self.assertRaisesRegex(
            RUNNER.BenchmarkError,
            "external-provenance-ledger.*required",
        ):
            RUNNER.main(["--external-bam-manifest", str(manifest)])

        ledger = self.root / "provenance.private.json"
        ledger.write_text(
            json.dumps(
                {
                    "schema": "dumi-external-provenance-ledger",
                    "version": 1,
                    "workloads": [
                        {
                            "workload_id": "demo-small",
                            "authorization_confirmed": True,
                            "pre_deduplication_confirmed": True,
                            "bam_sha256": entries[0].bam_sha256,
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        ledger_sha256 = sha256(ledger)
        ledger_path, receipt = (
            RUNNER.validate_external_provenance_ledger(
                path_string=str(ledger),
                expected_sha256=ledger_sha256,
                external_entries=entries,
            )
        )
        self.assertEqual(ledger_path, ledger.resolve())
        self.assertEqual(
            receipt,
            {
                "schema": "dumi-external-provenance-ledger",
                "version": 1,
                "sha256": ledger_sha256,
                "workload_count": 1,
                "authorization_confirmed": True,
                "pre_deduplication_confirmed": True,
                "path_recorded": False,
                "content_retained": False,
            },
        )
        self.assertNotIn("bam_sha256", receipt)
        self.assertNotIn(entries[0].bam_sha256, json.dumps(receipt))

        ledger.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(
            RUNNER.BenchmarkError, "SHA-256 does not match"
        ):
            RUNNER.verify_external_provenance_ledger_hash(
                ledger_path, ledger_sha256
            )
        with self.assertRaisesRegex(
            RUNNER.BenchmarkError, "regular non-symlink"
        ):
            RUNNER.validate_external_provenance_ledger(
                path_string=str(self.root / "missing-ledger.json"),
                expected_sha256="0" * 64,
                external_entries=entries,
            )

    def test_external_provenance_ledger_fails_closed_on_scope_or_consent(
        self,
    ) -> None:
        entries = RUNNER.parse_external_manifest(str(self.write_manifest()))
        ledger = self.root / "provenance.invalid.json"
        bam_sha256 = entries[0].bam_sha256

        cases = (
            (
                [
                    {
                        "workload_id": "demo-small",
                        "authorization_confirmed": False,
                        "pre_deduplication_confirmed": True,
                        "bam_sha256": bam_sha256,
                    }
                ],
                "does not confirm authorization",
            ),
            (
                [
                    {
                        "workload_id": "demo-small",
                        "authorization_confirmed": True,
                        "pre_deduplication_confirmed": False,
                        "bam_sha256": bam_sha256,
                    }
                ],
                "does not confirm pre-deduplication",
            ),
            (
                [
                    {
                        "workload_id": "demo-small",
                        "authorization_confirmed": True,
                        "pre_deduplication_confirmed": True,
                        "bam_sha256": bam_sha256,
                    },
                    {
                        "workload_id": "demo-small",
                        "authorization_confirmed": True,
                        "pre_deduplication_confirmed": True,
                        "bam_sha256": bam_sha256,
                    },
                ],
                "duplicate workload IDs",
            ),
            (
                [
                    {
                        "workload_id": "different-demo",
                        "authorization_confirmed": True,
                        "pre_deduplication_confirmed": True,
                        "bam_sha256": bam_sha256,
                    }
                ],
                "do not exactly match",
            ),
        )
        for workloads, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                ledger.write_text(
                    json.dumps(
                        {
                            "schema": "dumi-external-provenance-ledger",
                            "version": 1,
                            "workloads": workloads,
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    RUNNER.BenchmarkError, expected_error
                ):
                    RUNNER.validate_external_provenance_ledger(
                        path_string=str(ledger),
                        expected_sha256=sha256(ledger),
                        external_entries=entries,
                    )

        ledger.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(
            RUNNER.BenchmarkError,
            "provenance-ledger options require",
        ):
            RUNNER.main(
                [
                    "--external-provenance-ledger",
                    str(ledger),
                    "--external-provenance-ledger-sha256",
                    sha256(ledger),
                ]
            )

    def test_external_provenance_ledger_rejects_bam_hash_mismatch(
        self,
    ) -> None:
        entries = RUNNER.parse_external_manifest(str(self.write_manifest()))
        mismatched_sha256 = (
            "0" * 64 if entries[0].bam_sha256 != "0" * 64 else "1" * 64
        )
        ledger = self.root / "provenance.mismatch.json"
        ledger.write_text(
            json.dumps(
                {
                    "schema": "dumi-external-provenance-ledger",
                    "version": 1,
                    "workloads": [
                        {
                            "workload_id": "demo-small",
                            "authorization_confirmed": True,
                            "pre_deduplication_confirmed": True,
                            "bam_sha256": mismatched_sha256,
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            RUNNER.BenchmarkError,
            "bam_sha256 does not exactly match",
        ):
            RUNNER.validate_external_provenance_ledger(
                path_string=str(ledger),
                expected_sha256=sha256(ledger),
                external_entries=entries,
            )

    def test_external_provenance_ledger_requires_exact_workload_fields(
        self,
    ) -> None:
        entries = RUNNER.parse_external_manifest(str(self.write_manifest()))
        valid_row = {
            "workload_id": "demo-small",
            "authorization_confirmed": True,
            "pre_deduplication_confirmed": True,
            "bam_sha256": entries[0].bam_sha256,
        }
        ledger = self.root / "provenance.fields.json"

        for missing_field in valid_row:
            with self.subTest(missing=missing_field):
                row = dict(valid_row)
                del row[missing_field]
                ledger.write_text(
                    json.dumps(
                        {
                            "schema": "dumi-external-provenance-ledger",
                            "version": 1,
                            "workloads": [row],
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    RUNNER.BenchmarkError,
                    rf"missing fields: {missing_field}",
                ):
                    RUNNER.validate_external_provenance_ledger(
                        path_string=str(ledger),
                        expected_sha256=sha256(ledger),
                        external_entries=entries,
                    )

        row = dict(valid_row)
        row["private_source_note"] = "must remain private"
        ledger.write_text(
            json.dumps(
                {
                    "schema": "dumi-external-provenance-ledger",
                    "version": 1,
                    "workloads": [row],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            RUNNER.BenchmarkError,
            "unknown fields: private_source_note",
        ):
            RUNNER.validate_external_provenance_ledger(
                path_string=str(ledger),
                expected_sha256=sha256(ledger),
                external_entries=entries,
            )

    def test_external_provenance_ledger_requires_lowercase_bam_hash(
        self,
    ) -> None:
        entries = RUNNER.parse_external_manifest(str(self.write_manifest()))
        ledger = self.root / "provenance.uppercase-hash.json"
        ledger.write_text(
            json.dumps(
                {
                    "schema": "dumi-external-provenance-ledger",
                    "version": 1,
                    "workloads": [
                        {
                            "workload_id": "demo-small",
                            "authorization_confirmed": True,
                            "pre_deduplication_confirmed": True,
                            "bam_sha256": "A" * 64,
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            RUNNER.BenchmarkError,
            "64 lowercase hexadecimal characters",
        ):
            RUNNER.validate_external_provenance_ledger(
                path_string=str(ledger),
                expected_sha256=sha256(ledger),
                external_entries=entries,
            )

    def test_manifest_rejects_ascii_umi_separator_outside_public_grammar(
        self,
    ) -> None:
        for invalid_separator in ("abc", "_________", "/", "-", "-_"):
            with self.subTest(separator=invalid_separator):
                manifest = self.write_manifest()
                manifest.write_text(
                    manifest.read_text(encoding="utf-8").replace(
                        "\t_\t", f"\t{invalid_separator}\t"
                    ),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    RUNNER.BenchmarkError,
                    r"must not start with '-'",
                ):
                    RUNNER.parse_external_manifest(str(manifest))

    def test_benchmark_readme_tracks_timing_design_v2_and_export_boundary(
        self,
    ) -> None:
        readme = (
            ROOT / "scripts" / "benchmark" / "README.md"
        ).read_text(encoding="utf-8")
        for required in (
            "The standard profile uses eight repetitions.",
            "--repetitions 8",
            "Williams design",
            "`raw` and `end_to_end_ready`",
            "two directly measured stages",
            "cannot begin with `-`",
            "Paired inputs are always ineligible",
            "only three treatments",
            "future work, not part of timing design v2",
            "GNU coreutils `sort`",
            "--sort-command",
            "`<GNU_SORT>`",
            "path-neutral `gnu_sort` version receipt",
            "tracked, clean, and byte-identical",
            "`harness_commit_binding`",
            "--external-provenance-ledger",
            "--external-provenance-ledger-sha256",
            "dumi-external-provenance-ledger",
            "path_recorded=false",
            "content_retained=false",
            "must not disclose the ledger hash",
            "Independent Directional-oracle gate",
            "dumi-directional-oracle-check-v1",
            "`not_comparable`",
            "`noncomparable_pairs=attempted_pairs`",
            "specifically attributable to N state",
            "restricted-cohort observations qualitative",
            "exhaustive and randomized",
            "synthetic oracle tests",
            "`-k 1 -p .5`",
            "`--edit-distance 1 --percentage 0.5`",
            "External cells",
            "UMI length and literal separator recorded in their input manifest",
            "unmodified runner-produced",
            "`STATUS.json` must say `COMPLETE`",
            "`MANIFEST.sha256` must verify",
            "--alias-map",
            "--private-denylist",
            "--private-export-receipt",
            "A human reviewer must",
        ):
            self.assertIn(required, readme)
        for retired in (
            "seven repetitions",
            "--repetitions 7",
            "repeated cyclic Latin schedule",
            "`raw_plus_ready`",
        ):
            self.assertNotIn(retired, readme)

    def test_external_harness_requires_clean_commit_identical_sources(
        self,
    ) -> None:
        git_path = shutil.which("git")
        if git_path is None:
            self.skipTest("git is required for harness provenance validation")
        git = Path(git_path).resolve()
        repository = self.root / "repository"
        source = repository / "scripts" / "benchmark" / "run_benchmark.py"
        source.parent.mkdir(parents=True)
        source.write_text("committed harness\n", encoding="utf-8")
        subprocess.run(
            [git, "-C", repository, "init"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run([git, "-C", repository, "add", "."], check=True)
        subprocess.run(
            [
                git,
                "-C",
                repository,
                "-c",
                "user.name=Benchmark Test",
                "-c",
                "user.email=benchmark@example.invalid",
                "commit",
                "-m",
                "fixture",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        dumi_sha = (
            subprocess.run(
                [git, "-C", repository, "rev-parse", "HEAD"],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout.strip()
        )

        output_root = self.root / "evidence"
        harness_root = output_root / "harness"
        archived_root = output_root / "sources" / "dumi"
        snapshot = harness_root / source.name
        archived = archived_root / source.relative_to(repository)
        snapshot.parent.mkdir(parents=True)
        archived.parent.mkdir(parents=True)
        shutil.copy2(source, snapshot)
        shutil.copy2(source, archived)

        binding = RUNNER.verify_external_harness_commit_binding(
            git=git,
            repository_root=repository,
            dumi_sha=dumi_sha,
            harness_sources=(source,),
            harness_snapshot_root=harness_root,
            archived_dumi_root=archived_root,
            output_root=output_root,
        )
        self.assertEqual(
            binding,
            {
                "status": "verified",
                "repository_url": RUNNER.DUMI_PUBLIC_URL,
                "commit_sha": dumi_sha,
                "files": [
                    {
                        "repository_path": (
                            "scripts/benchmark/run_benchmark.py"
                        ),
                        "snapshot_path": "harness/run_benchmark.py",
                        "sha256": sha256(snapshot),
                    }
                ],
            },
        )

        source.write_text("dirty harness\n", encoding="utf-8")
        with self.assertRaisesRegex(
            RUNNER.BenchmarkError, "not tracked and clean"
        ):
            RUNNER.verify_external_harness_commit_binding(
                git=git,
                repository_root=repository,
                dumi_sha=dumi_sha,
                harness_sources=(source,),
                harness_snapshot_root=harness_root,
                archived_dumi_root=archived_root,
                output_root=output_root,
            )

        source.write_text("committed harness\n", encoding="utf-8")
        snapshot.write_text("tampered snapshot\n", encoding="utf-8")
        with self.assertRaisesRegex(
            RUNNER.BenchmarkError, "not byte-identical"
        ):
            RUNNER.verify_external_harness_commit_binding(
                git=git,
                repository_root=repository,
                dumi_sha=dumi_sha,
                harness_sources=(source,),
                harness_snapshot_root=harness_root,
                archived_dumi_root=archived_root,
                output_root=output_root,
            )

    def test_all_input_touching_stdout_and_stderr_logs_are_redacted(self) -> None:
        output_root = self.root / "evidence"
        expected_logs = (
            "contracts/external/demo-small/default-stdout.txt",
            "contracts/external/demo-small/forced-on-stderr.txt",
            "input-validation/external/demo-small/records-stderr.txt",
            "oracles/external/demo-small/stdout.txt",
            "runs/external/demo-small/raw/preread-stdout.txt",
            "runs/external/demo-small/raw/stderr.txt",
            "warmups/external/demo-small/stderr.txt",
        )
        private_token = "private-qname-token"
        for relative in expected_logs:
            path = output_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(private_token + "\n", encoding="utf-8")
        command = output_root / "contracts" / "external" / "demo-small" / "command.txt"
        command.write_text("path-safe command receipt\n", encoding="utf-8")

        RUNNER.redact_external_execution_logs(output_root)

        for relative in expected_logs:
            text = (output_root / relative).read_text(encoding="utf-8")
            self.assertNotIn(private_token, text)
            self.assertIn("suppressed after validation", text)
        self.assertEqual(
            command.read_text(encoding="utf-8"), "path-safe command receipt\n"
        )

    def test_public_evidence_scan_rejects_exact_external_source_path(self) -> None:
        output_root = self.root / "evidence"
        output_root.mkdir()
        leaked_path = str(self.bam.resolve())
        (output_root / "receipt.txt").write_text(
            f"source={leaked_path}\n", encoding="utf-8"
        )
        RUNNER.PUBLIC_PATH_REPLACEMENTS = [
            (leaked_path, "<EXTERNAL_BAM:demo-small>")
        ]

        with self.assertRaisesRegex(
            RUNNER.BenchmarkError, "public-evidence privacy scan failed"
        ):
            RUNNER.scan_public_evidence(output_root, ROOT)

    def test_private_tool_prefixes_sanitize_environment_receipts(self) -> None:
        samtools_prefix = Path("/").joinpath(
            "mnt", "private", "samtools-env"
        )
        java_prefix = Path("/").joinpath(
            "Users", "private user", "jdk"
        )
        tools = (
            ("SAMTOOLS", samtools_prefix / "bin" / "samtools"),
            ("JAVA", java_prefix / "bin" / "java"),
            ("GIT", Path("/usr/bin/git")),
        )
        RUNNER.PUBLIC_PATH_REPLACEMENTS = [
            *[
                (str(path), f"<{label}>")
                for label, path in tools
            ],
            *RUNNER.private_tool_prefix_replacements(tools),
        ]
        raw = {
            "subprocess_environment": {
                "PATH": (
                    f"{samtools_prefix}/bin:"
                    f"{java_prefix}/bin:/usr/bin"
                )
            },
            "samtools": "\n".join(
                (
                    "samtools 1.20",
                    f"CPPFLAGS=-I{samtools_prefix}/include -O2",
                    (
                        f"LDFLAGS=-L{samtools_prefix}/lib "
                        f"-Wl,-rpath,{samtools_prefix}/lib"
                    ),
                    (
                        "-fdebug-prefix-map="
                        f"{samtools_prefix}/build=/usr/local/src/samtools"
                    ),
                )
            ),
        }

        sanitized = RUNNER.sanitize_public_text(json.dumps(raw))

        self.assertIn("<SAMTOOLS_PREFIX>/bin", sanitized)
        self.assertIn("<JAVA_PREFIX>/bin", sanitized)
        self.assertIn("-I<SAMTOOLS_PREFIX>/include", sanitized)
        self.assertIn("-L<SAMTOOLS_PREFIX>/lib", sanitized)
        self.assertIn(
            "<SAMTOOLS_PREFIX>/build=/usr/local/src/samtools",
            sanitized,
        )
        sibling_path = str(
            samtools_prefix.parent / "samtools-env-copy" / "include"
        )
        self.assertEqual(
            RUNNER.sanitize_public_text(sibling_path),
            sibling_path,
        )
        for root_name in ("mnt", "home", "Users"):
            private_root = f"/{root_name}/"
            self.assertNotIn(private_root, sanitized)

        output_root = self.root / "evidence"
        output_root.mkdir()
        for filename in ("environment.json", "environment.txt", "manifest.json"):
            (output_root / filename).write_text(
                sanitized + "\n", encoding="utf-8"
            )
        RUNNER.scan_public_evidence(output_root, ROOT)

    def test_tracked_source_snapshot_has_no_private_root_literals(self) -> None:
        completed = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        private_roots = tuple(
            f"/{root_name}/" for root_name in ("mnt", "home", "Users")
        )
        offenders: list[str] = []
        for relative_bytes in completed.stdout.split(b"\0"):
            if not relative_bytes:
                continue
            relative = relative_bytes.decode("utf-8")
            path = ROOT / relative
            if not path.is_file():
                continue
            payload = path.read_bytes()
            if b"\0" in payload:
                continue
            text = payload.decode("utf-8", errors="replace")
            if any(private_root in text for private_root in private_roots):
                offenders.append(relative)
        self.assertEqual(offenders, [])

    def test_unexpected_private_sam_cannot_survive_external_sealing(self) -> None:
        output_root = self.root / "evidence"
        private_sam = output_root / "oracles" / "private-path.sam"
        source_fixture = (
            output_root
            / "sources"
            / "dumi"
            / "test"
            / "fixtures"
            / "public-fixture.sam"
        )
        private_sam.parent.mkdir(parents=True)
        source_fixture.parent.mkdir(parents=True)
        private_sam.write_text(
            f"@CO\tprivate source {self.bam.resolve()}\n",
            encoding="utf-8",
        )
        source_fixture.write_text("@HD\tVN:1.6\n", encoding="utf-8")
        with self.assertRaisesRegex(
            RUNNER.BenchmarkError, "retained alignment artifacts"
        ):
            RUNNER.require_no_alignment_artifacts(output_root)
        RUNNER.cleanup_external_alignment_artifacts(output_root)
        self.assertFalse(private_sam.exists())
        self.assertTrue(source_fixture.is_file())
        RUNNER.require_no_alignment_artifacts(output_root)

    def test_cached_oracle_comparison_is_byte_exact(self) -> None:
        if self.samtools is None:
            self.skipTest("samtools is required for semantic comparison")
        canonical = self.root / "oracle.records.sorted.private"
        canonical_receipt = self.root / "oracle.canonical-receipt.private"
        first = subprocess.run(
            [
                sys.executable,
                CHECKER_PATH,
                "--samtools",
                self.samtools,
                "--canonical-output",
                canonical,
                "--canonical-receipt-output",
                canonical_receipt,
                self.bam,
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(json.loads(first.stdout)["quickcheck_status"], "pass")
        self.assertTrue(canonical.is_file())
        self.assertTrue(canonical_receipt.is_file())

        second = subprocess.run(
            [
                sys.executable,
                CHECKER_PATH,
                "--samtools",
                self.samtools,
                "--reference",
                self.bam,
                "--reference-canonical",
                canonical,
                "--reference-canonical-receipt",
                canonical_receipt,
                self.bam,
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        result = json.loads(second.stdout)
        self.assertTrue(result["record_equivalent"])
        self.assertTrue(result["reference_dictionary_equivalent"])


if __name__ == "__main__":
    unittest.main()
