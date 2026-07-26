#!/usr/bin/env python3
"""Focused tests for the source-derived Directional oracle gate."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = (
    ROOT / "scripts" / "benchmark" / "directional_oracle_check.py"
)
SPEC = importlib.util.spec_from_file_location(
    "directional_oracle_check",
    HELPER_PATH,
)
assert SPEC is not None and SPEC.loader is not None
ORACLE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ORACLE
SPEC.loader.exec_module(ORACLE)


def integration_tools() -> tuple[bool, str]:
    if shutil.which("samtools") is None:
        return False, "samtools is required for Directional-oracle integration tests"
    sort_command = shutil.which("gsort") or shutil.which("sort")
    if sort_command is None:
        return False, "GNU sort is required for Directional-oracle integration tests"
    completed = subprocess.run(
        [sort_command, "--version"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0 or "GNU coreutils" not in completed.stdout:
        return False, "GNU coreutils sort is required"
    return True, ""


INTEGRATION_AVAILABLE, INTEGRATION_SKIP_REASON = integration_tools()
requires_integration = unittest.skipUnless(
    INTEGRATION_AVAILABLE,
    INTEGRATION_SKIP_REASON,
)


HEADER = (
    "@HD\tVN:1.6\tSO:coordinate\n"
    "@SQ\tSN:chr1\tLN:100000\n"
    "@RG\tID:private-rg\tSM:private-sample\n"
)


def sam_record(
    qname: str,
    *,
    mi: int | None = None,
    rx: str | None = None,
    pos: int = 100,
    flag: int = 0,
) -> str:
    fields = [
        qname,
        str(flag),
        "chr1",
        str(pos),
        "60",
        "50M",
        "*",
        "0",
        "0",
        "A" * 50,
        "I" * 50,
    ]
    if mi is not None:
        fields.append(f"MI:Z:{mi}")
    if rx is not None:
        fields.append(f"RX:Z:{rx}")
    return "\t".join(fields) + "\n"


def write_sam(
    path: Path,
    records: list[str],
    *,
    header: str = HEADER,
) -> None:
    path.write_text(header + "".join(records), encoding="ascii")


def base_source_records() -> list[str]:
    return [
        sam_record("private-a1_AAAA"),
        sam_record("private-a2_AAAA"),
        sam_record("private-a3_AAAA"),
        sam_record("private-t1_AAAT"),
        sam_record("private-c1_CCCC"),
    ]


def passing_tagged_records(
    *,
    root: str = "AAAA",
) -> list[str]:
    return [
        sam_record("private-a1_AAAA", mi=17, rx=root),
        sam_record("private-a2_AAAA", mi=17, rx=root),
        sam_record("private-a3_AAAA", mi=17, rx=root),
        sam_record("private-t1_AAAT", mi=17, rx=root),
        sam_record("private-c1_CCCC", mi=91, rx="CCCC"),
    ]


class DirectionalOracleCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(
            prefix="dumi-directional-oracle-test-"
        )
        self.directory = Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def run_helper(
        self,
        upstream_records: list[str],
        dumi_records: list[str],
        *,
        source_records: list[str] | None = None,
        source_header: str = HEADER,
        upstream_header: str = HEADER,
        dumi_header: str = HEADER,
        extra_arguments: list[str] | None = None,
        receipt_name: str = "receipt.json",
    ) -> tuple[int, dict[str, object] | None, subprocess.CompletedProcess[str]]:
        source = self.directory / "private-source.sam"
        upstream = self.directory / "private-upstream.sam"
        dumi = self.directory / "private-dumi.sam"
        receipt = self.directory / receipt_name
        write_sam(
            source,
            source_records if source_records is not None else base_source_records(),
            header=source_header,
        )
        write_sam(upstream, upstream_records, header=upstream_header)
        write_sam(dumi, dumi_records, header=dumi_header)
        command = [
            sys.executable,
            os.fspath(HELPER_PATH),
            os.fspath(source),
            os.fspath(upstream),
            os.fspath(dumi),
            "--receipt",
            os.fspath(receipt),
            "--umi-length",
            "4",
            "--tmpdir",
            os.fspath(self.directory),
            "--sort-buffer-size",
            "1M",
        ]
        if extra_arguments:
            command.extend(extra_arguments)
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        payload = None
        if receipt.exists():
            try:
                payload = json.loads(receipt.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = None
        return completed.returncode, payload, completed

    def test_binary32_threshold_preserves_java_rounding(self) -> None:
        # 16,777,219 rounds upward when converted to binary32.  A Python
        # integer formula would incorrectly return 8,388,609.
        self.assertEqual(
            ORACLE.directional_threshold(16_777_218, 0.5),
            8_388_610,
        )
        self.assertNotEqual(
            ORACLE.directional_threshold(16_777_218, 0.5),
            (16_777_218 + 1) // 2,
        )

    def test_arbitrary_length_tie_order_uses_signed_chunks_in_array_order(
        self,
    ) -> None:
        zero_22 = ORACLE.encoded_tie_key(b"A" * 22)
        signed_boundary = ORACLE.encoded_tie_key(b"A" * 21 + b"T")
        # Base 22 begins at bit 63.  Java Long.compare therefore treats the
        # first chunk as negative even though a later chunk is also nonzero.
        self.assertLess(signed_boundary, zero_22)

        zero_23 = ORACLE.encoded_tie_key(b"A" * 23)
        second_chunk = ORACLE.encoded_tie_key(b"A" * 22 + b"T")
        self.assertLess(zero_23, second_chunk)
        self.assertEqual(len(second_chunk[0]), 2)

    def test_n_is_distinct_in_distance_and_total_order(self) -> None:
        frequencies = {b"ANNN": 2, b"ANNA": 1, b"AAAA": 1}
        clusters = ORACLE.directional_clusters(frequencies, 0.5)
        self.assertEqual(clusters[0][0], b"ANNN")
        self.assertIn(b"ANNA", clusters[0][1])
        self.assertNotEqual(
            ORACLE.encoded_tie_key(b"ANNN"),
            ORACLE.encoded_tie_key(b"AAAA"),
        )

    def test_equal_frequency_shared_neighbor_uses_total_order(self) -> None:
        clusters = ORACLE.directional_clusters(
            {b"AAAA": 2, b"AATT": 2, b"AAAT": 1},
            0.5,
        )
        self.assertEqual(
            clusters,
            [
                (b"AAAA", (b"AAAA", b"AAAT")),
                (b"AATT", (b"AATT",)),
            ],
        )

    def test_directional_closure_is_transitive(self) -> None:
        clusters = ORACLE.directional_clusters(
            {b"AAAA": 8, b"AAAT": 4, b"AATT": 2, b"ATTT": 1},
            0.5,
        )
        self.assertEqual(
            clusters,
            [(b"AAAA", (b"AAAA", b"AAAT", b"AATT", b"ATTT"))],
        )

    def test_oracle_rejects_mixed_umi_lengths(self) -> None:
        with self.assertRaisesRegex(
            ORACLE.OracleCheckError,
            "fixed UMI length",
        ):
            ORACLE.directional_clusters({b"AAAA": 2, b"AAA": 1}, 0.5)

    @requires_integration
    def test_dumi_exact_oracle_match_passes_with_v1_receipt(self) -> None:
        tagged = passing_tagged_records()
        code, receipt, completed = self.run_helper(tagged, tagged)
        self.assertEqual(code, 0, completed.stderr)
        assert receipt is not None
        self.assertEqual(receipt["schema"], "dumi-directional-oracle-check-v1")
        self.assertEqual(receipt["version"], 1)
        self.assertTrue(receipt["gate"]["directional_oracle_gate_pass"])
        self.assertTrue(
            receipt["gate"]["dumi_off_oracle_partition_equivalent"]
        )
        self.assertTrue(
            receipt["gate"]["dumi_off_oracle_root_assignment_equivalent"]
        )
        self.assertEqual(receipt["source_oracle"]["eligible_records"], 5)
        self.assertEqual(receipt["source_oracle"]["clusters"], 2)
        oracle_bytes = (
            receipt["source_oracle"]["membership_partition_bytes"]
            + receipt["source_oracle"]["rooted_partition_bytes"]
        )
        upstream_bytes = (
            receipt["canonical_upstream"]["membership_partition_bytes"]
            + receipt["canonical_upstream"]["rooted_partition_bytes"]
        )
        dumi_bytes = (
            receipt["dumi_off"]["membership_partition_bytes"]
            + receipt["dumi_off"]["rooted_partition_bytes"]
        )
        oracle_multiset = receipt["source_oracle"][
            "alignment_umi_frequency_multiset_bytes"
        ]
        upstream_multiset = receipt["canonical_upstream"][
            "alignment_umi_frequency_multiset_bytes"
        ]
        dumi_multiset = receipt["dumi_off"][
            "alignment_umi_frequency_multiset_bytes"
        ]
        self.assertEqual(
            receipt["temporary_storage"][
                "persistent_stage_peak_upper_bound_bytes"
            ],
            max(
                receipt["source_oracle"]["record_key_bytes"]
                + oracle_bytes
                + oracle_multiset,
                oracle_bytes
                + receipt["canonical_upstream"]["record_key_bytes"]
                + upstream_bytes
                + upstream_multiset,
                oracle_bytes
                + upstream_bytes
                + receipt["dumi_off"]["record_key_bytes"]
                + dumi_bytes
                + dumi_multiset,
            ),
        )
        self.assertEqual(stat.S_IMODE((self.directory / "receipt.json").stat().st_mode), 0o600)
        self.assertEqual(
            list(self.directory.glob(".dumi-cluster-partition-*")),
            [],
        )

    @requires_integration
    def test_upstream_root_difference_is_diagnostic_not_a_gate(self) -> None:
        upstream = passing_tagged_records(root="AAAT")
        dumi = passing_tagged_records()
        code, receipt, completed = self.run_helper(upstream, dumi)
        self.assertEqual(code, 0, completed.stderr)
        assert receipt is not None
        self.assertTrue(receipt["gate"]["directional_oracle_gate_pass"])
        self.assertTrue(
            receipt["diagnostics"][
                "canonical_upstream_oracle_partition_equivalent"
            ]
        )
        self.assertFalse(
            receipt["diagnostics"][
                "canonical_upstream_oracle_root_assignment_equivalent"
            ]
        )

    @requires_integration
    def test_upstream_membership_difference_is_diagnostic_not_a_gate(
        self,
    ) -> None:
        upstream = [
            sam_record("private-a1_AAAA", mi=1, rx="AAAA"),
            sam_record("private-a2_AAAA", mi=1, rx="AAAA"),
            sam_record("private-a3_AAAA", mi=1, rx="AAAA"),
            sam_record("private-t1_AAAT", mi=2, rx="AAAT"),
            sam_record("private-c1_CCCC", mi=3, rx="CCCC"),
        ]
        code, receipt, completed = self.run_helper(
            upstream,
            passing_tagged_records(),
        )
        self.assertEqual(code, 0, completed.stderr)
        assert receipt is not None
        self.assertTrue(receipt["gate"]["directional_oracle_gate_pass"])
        self.assertFalse(
            receipt["diagnostics"][
                "canonical_upstream_oracle_partition_equivalent"
            ]
        )

    @requires_integration
    def test_dumi_membership_difference_writes_receipt_and_fails_gate(
        self,
    ) -> None:
        split = [
            sam_record("private-a1_AAAA", mi=1, rx="AAAA"),
            sam_record("private-a2_AAAA", mi=1, rx="AAAA"),
            sam_record("private-a3_AAAA", mi=1, rx="AAAA"),
            sam_record("private-t1_AAAT", mi=2, rx="AAAT"),
            sam_record("private-c1_CCCC", mi=3, rx="CCCC"),
        ]
        code, receipt, _ = self.run_helper(
            passing_tagged_records(),
            split,
        )
        self.assertEqual(code, 1)
        assert receipt is not None
        self.assertFalse(receipt["gate"]["directional_oracle_gate_pass"])
        self.assertFalse(
            receipt["gate"]["dumi_off_oracle_partition_equivalent"]
        )

    @requires_integration
    def test_dumi_root_only_difference_fails_gate(self) -> None:
        code, receipt, _ = self.run_helper(
            passing_tagged_records(),
            passing_tagged_records(root="AAAT"),
        )
        self.assertEqual(code, 1)
        assert receipt is not None
        self.assertTrue(
            receipt["gate"]["dumi_off_oracle_partition_equivalent"]
        )
        self.assertFalse(
            receipt["gate"]["dumi_off_oracle_root_assignment_equivalent"]
        )

    @requires_integration
    def test_tagged_record_multiset_drift_is_operational_failure(self) -> None:
        upstream = passing_tagged_records()
        upstream[3] = sam_record("private-t1_AATA", mi=17, rx="AAAA")
        code, receipt, completed = self.run_helper(
            upstream,
            passing_tagged_records(),
        )
        self.assertEqual(code, 2)
        self.assertIsNone(receipt)
        self.assertIn("alignment/UMI record multiset", completed.stderr)

    @requires_integration
    def test_dumi_header_difference_fails_even_when_partition_matches(
        self,
    ) -> None:
        tagged = passing_tagged_records()
        changed_header = HEADER.replace("private-sample", "other-private-sample")
        code, receipt, _ = self.run_helper(
            tagged,
            tagged,
            dumi_header=changed_header,
        )
        self.assertEqual(code, 1)
        assert receipt is not None
        self.assertTrue(
            receipt["gate"]["dumi_off_oracle_partition_equivalent"]
        )
        self.assertFalse(
            receipt["gate"][
                "dumi_off_source_read_group_dictionary_equivalent"
            ]
        )

    @requires_integration
    def test_upstream_header_corruption_is_an_operational_failure(self) -> None:
        tagged = passing_tagged_records()
        changed_header = HEADER.replace("LN:100000", "LN:99999")
        code, receipt, completed = self.run_helper(
            tagged,
            tagged,
            upstream_header=changed_header,
        )
        self.assertEqual(code, 2)
        self.assertIsNone(receipt)
        self.assertIn("ordered reference dictionary", completed.stderr)

    @requires_integration
    def test_receipt_does_not_disclose_qnames_headers_or_paths(self) -> None:
        private_marker = "never-publish-private-source-77"
        header = HEADER.replace("private-sample", private_marker)
        source = [
            record.replace("private-", f"{private_marker}-")
            for record in base_source_records()
        ]
        tagged = [
            record.replace("private-", f"{private_marker}-")
            for record in passing_tagged_records()
        ]
        code, receipt, completed = self.run_helper(
            tagged,
            tagged,
            source_records=source,
            source_header=header,
            upstream_header=header,
            dumi_header=header,
        )
        self.assertEqual(code, 0, completed.stderr)
        assert receipt is not None
        serialized = json.dumps(receipt, sort_keys=True)
        combined = serialized + completed.stdout + completed.stderr
        self.assertNotIn(private_marker, combined)
        self.assertNotIn("private-source.sam", combined)
        self.assertNotIn("private-upstream.sam", combined)
        self.assertNotIn("private-dumi.sam", combined)

    def test_unsupported_edit_distance_is_operational_error(self) -> None:
        source = self.directory / "source.sam"
        upstream = self.directory / "upstream.sam"
        dumi = self.directory / "dumi.sam"
        receipt = self.directory / "invalid.json"
        for path in (source, upstream, dumi):
            write_sam(path, base_source_records())
        code = ORACLE.main(
            [
                os.fspath(source),
                os.fspath(upstream),
                os.fspath(dumi),
                "--receipt",
                os.fspath(receipt),
                "--umi-length",
                "4",
                "--edit-distance",
                "2",
            ]
        )
        self.assertEqual(code, 2)
        self.assertFalse(receipt.exists())

        code = ORACLE.main(
            [
                os.fspath(source),
                os.fspath(upstream),
                os.fspath(dumi),
                "--receipt",
                os.fspath(receipt),
                "--umi-length",
                "4",
                "--percentage",
                "0.500000029802322387695312500000000001",
            ]
        )
        self.assertEqual(code, 2)
        self.assertFalse(receipt.exists())

        code = ORACLE.main(
            [
                os.fspath(source),
                os.fspath(upstream),
                os.fspath(dumi),
                "--receipt",
                os.fspath(receipt),
                "--umi-length",
                "1",
            ]
        )
        self.assertEqual(code, 2)
        self.assertFalse(receipt.exists())

    @requires_integration
    def test_hardlinked_roles_are_rejected(self) -> None:
        source = self.directory / "source.sam"
        upstream = self.directory / "upstream.sam"
        dumi = self.directory / "dumi.sam"
        receipt = self.directory / "hardlink.json"
        write_sam(source, base_source_records())
        os.link(source, upstream)
        write_sam(dumi, passing_tagged_records())
        code = ORACLE.main(
            [
                os.fspath(source),
                os.fspath(upstream),
                os.fspath(dumi),
                "--receipt",
                os.fspath(receipt),
                "--umi-length",
                "4",
                "--tmpdir",
                os.fspath(self.directory),
                "--sort-buffer-size",
                "1M",
            ]
        )
        self.assertEqual(code, 2)
        self.assertFalse(receipt.exists())

    @unittest.skipUnless(
        hasattr(signal, "SIGTERM") and hasattr(signal, "SIGHUP"),
        "POSIX termination signals are required",
    )
    def test_signal_exit_status_and_private_workspace_cleanup(self) -> None:
        child_program = r"""
import importlib.util
import os
from pathlib import Path
import signal
import sys

helper_path = Path(sys.argv[1])
temporary_parent = Path(sys.argv[2])
specification = importlib.util.spec_from_file_location(
    "directional_oracle_signal_child", helper_path
)
assert specification is not None and specification.loader is not None
helper = importlib.util.module_from_spec(specification)
sys.modules[specification.name] = helper
specification.loader.exec_module(helper)
os.umask(0o077)

def replacement_main():
    checker = helper.load_partition_checker(
        helper_path.with_name("cluster_partition_check.py")
    )
    with checker.private_workspace(temporary_parent) as workspace:
        print(workspace, flush=True)
        signal.pause()
    return 0

helper.main = replacement_main
raise SystemExit(helper.cli_entrypoint())
"""
        for signal_number in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=signal.Signals(signal_number).name):
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        child_program,
                        os.fspath(HELPER_PATH),
                        os.fspath(self.directory),
                    ],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                try:
                    assert process.stdout is not None
                    workspace_text = process.stdout.readline().strip()
                    self.assertTrue(workspace_text)
                    workspace = Path(workspace_text)
                    self.assertTrue(workspace.is_dir())
                    os.kill(process.pid, signal_number)
                    process.wait(timeout=10)
                    self.assertEqual(
                        process.returncode,
                        128 + signal_number,
                    )
                    self.assertFalse(workspace.exists())
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                    if process.stdout is not None:
                        process.stdout.close()
                    if process.stderr is not None:
                        process.stderr.close()

    @requires_integration
    def test_existing_receipt_is_not_overwritten(self) -> None:
        receipt = self.directory / "receipt.json"
        receipt.write_text("sentinel", encoding="ascii")
        code, payload, _ = self.run_helper(
            passing_tagged_records(),
            passing_tagged_records(),
        )
        self.assertEqual(code, 2)
        self.assertIsNone(payload)
        self.assertEqual(receipt.read_text(encoding="ascii"), "sentinel")


if __name__ == "__main__":
    unittest.main()
