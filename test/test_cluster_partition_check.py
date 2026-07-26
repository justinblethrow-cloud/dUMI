#!/usr/bin/env python3
"""Focused adversarial tests for the tag-derived cluster-partition gate."""

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
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CHECKER_PATH = ROOT / "scripts" / "benchmark" / "cluster_partition_check.py"
SPEC = importlib.util.spec_from_file_location("cluster_partition_check", CHECKER_PATH)
assert SPEC is not None and SPEC.loader is not None
CHECKER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CHECKER
SPEC.loader.exec_module(CHECKER)


def cluster_integration_tools() -> tuple[bool, str]:
    """Report whether the helper's real external-tool contract is available."""

    if shutil.which("samtools") is None:
        return False, "samtools is required for cluster-partition integration tests"
    sort_command = shutil.which("gsort") or shutil.which("sort")
    if sort_command is None:
        return False, "GNU sort is required for cluster-partition integration tests"
    try:
        completed = subprocess.run(
            [sort_command, "--version"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError:
        return False, "GNU sort is required for cluster-partition integration tests"
    if completed.returncode != 0 or "GNU coreutils" not in completed.stdout:
        return (
            False,
            "GNU sort with --buffer-size support is required for "
            "cluster-partition integration tests",
        )
    return True, ""


CLUSTER_INTEGRATION_AVAILABLE, CLUSTER_INTEGRATION_SKIP_REASON = (
    cluster_integration_tools()
)
requires_cluster_integration = unittest.skipUnless(
    CLUSTER_INTEGRATION_AVAILABLE,
    CLUSTER_INTEGRATION_SKIP_REASON,
)


PRIVATE_HEADER = (
    "@HD\tVN:1.6\tSO:unsorted\n"
    "@SQ\tSN:chr1\tLN:100000\n"
    "@RG\tID:private-rg\tSM:private-sample\n"
)


def sam_record(
    qname: str,
    *,
    flag: int = 0,
    pos: int = 100,
    cigar: str = "50M",
    mate_reference: str = "*",
    mate_pos: int = 0,
    tlen: int = 0,
    mi: int | None = 0,
    rx: str = "AAAA",
    extra: str = "",
    read_length: int = 50,
) -> str:
    tags = []
    if mi is not None:
        tags.append(f"MI:Z:{mi}")
    tags.append(f"RX:Z:{rx}")
    if extra:
        tags.append(extra)
    return (
        f"{qname}\t{flag}\tchr1\t{pos}\t60\t{cigar}\t{mate_reference}\t"
        f"{mate_pos}\t{tlen}\t{'A' * read_length}\t{'I' * read_length}\t"
        + "\t".join(tags)
        + "\n"
    )


def write_sam(path: Path, records: list[str], *, header: str = PRIVATE_HEADER) -> None:
    path.write_text(header + "".join(records), encoding="ascii")


class ClusterPartitionCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(
            prefix="dumi-partition-test-"
        )
        self.directory = Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_gnu_sort_resolution_prefers_gsort_and_rejects_bsd(self) -> None:
        gnu_version = subprocess.CompletedProcess(
            ["/opt/homebrew/bin/gsort", "--version"],
            0,
            "sort (GNU coreutils) 9.5\n",
            "",
        )
        with (
            mock.patch.object(
                CHECKER.shutil,
                "which",
                side_effect=lambda candidate: (
                    "/opt/homebrew/bin/gsort"
                    if candidate == "gsort"
                    else "/usr/bin/sort"
                ),
            ),
            mock.patch.object(
                CHECKER.subprocess,
                "run",
                return_value=gnu_version,
            ) as run,
        ):
            self.assertEqual(
                CHECKER.resolve_gnu_sort(None),
                "/opt/homebrew/bin/gsort",
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
                CHECKER.shutil, "which", return_value="/usr/bin/sort"
            ),
            mock.patch.object(
                CHECKER.subprocess,
                "run",
                return_value=bsd_version,
            ),
            self.assertRaisesRegex(
                CHECKER.PartitionCheckError,
                "GNU sort is required",
            ),
        ):
            CHECKER.resolve_gnu_sort("sort")

    def run_checker(
        self,
        left_records: list[str],
        right_records: list[str],
        *,
        left_header: str = PRIVATE_HEADER,
        right_header: str = PRIVATE_HEADER,
        extra_arguments: list[str] | None = None,
        receipt_name: str = "receipt.json",
    ) -> tuple[int, dict[str, object] | None, subprocess.CompletedProcess[str]]:
        left = self.directory / "private-left.sam"
        right = self.directory / "private-right.sam"
        receipt = self.directory / receipt_name
        write_sam(left, left_records, header=left_header)
        write_sam(right, right_records, header=right_header)
        command = [
            sys.executable,
            os.fspath(CHECKER_PATH),
            os.fspath(left),
            os.fspath(right),
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
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        payload = (
            json.loads(receipt.read_text(encoding="utf-8"))
            if receipt.exists()
            else None
        )
        return completed.returncode, payload, completed

    @requires_cluster_integration
    def test_mi_root_representative_qname_and_record_reordering_pass(self) -> None:
        left = [
            sam_record("left-a1_AAAA", mi=0, rx="AAAA", extra="cs:i:4"),
            sam_record("left-a2_AAAA", flag=1024, mi=0, rx="AAAA"),
            sam_record("left-a3_AAAA", flag=1024, mi=0, rx="AAAA"),
            sam_record("left-b_AAAT", flag=1024, mi=0, rx="AAAA"),
            sam_record("left-c1_CCCC", mi=1, rx="CCCC", extra="cs:i:2"),
            sam_record("left-c2_CCCC", flag=1024, mi=1, rx="CCCC"),
        ]
        right = [
            sam_record("right-c2_CCCC", mi=41, rx="CCCC"),
            sam_record("right-b_AAAT", mi=99, rx="AAAT", extra="cs:i:1"),
            sam_record("right-a3_AAAA", flag=1024, mi=99, rx="AAAT"),
            sam_record("right-c1_CCCC", flag=1024, mi=41, rx="CCCC"),
            sam_record("right-a1_AAAA", flag=1024, mi=99, rx="AAAT"),
            sam_record("right-a2_AAAA", mi=99, rx="AAAT", extra="cs:i:4"),
        ]
        right_header = PRIVATE_HEADER + "@PG\tID:different-implementation\n"
        code, receipt, _ = self.run_checker(
            left, right, right_header=right_header
        )
        self.assertEqual(code, 0)
        assert receipt is not None
        self.assertTrue(receipt["equivalent"])
        self.assertTrue(receipt["partition_equivalent"])
        self.assertEqual(receipt["left"]["clusters"], 2)
        self.assertEqual(receipt["left"]["umi_memberships"], 3)
        self.assertEqual(
            receipt["left"]["partition_cluster_multiset_sha256"],
            receipt["right"]["partition_cluster_multiset_sha256"],
        )

    @requires_cluster_integration
    def test_changed_partition_with_same_cluster_count_fails(self) -> None:
        left = [
            sam_record("a_AAAA", mi=0, rx="AAAA"),
            sam_record("b_AAAT", mi=0, rx="AAAA"),
            sam_record("c_CCCC", mi=1, rx="CCCC"),
            sam_record("d_CCCT", mi=1, rx="CCCC"),
        ]
        right = [
            sam_record("a_AAAA", mi=10, rx="AAAA"),
            sam_record("c_CCCC", mi=10, rx="AAAA"),
            sam_record("b_AAAT", mi=11, rx="AAAT"),
            sam_record("d_CCCT", mi=11, rx="AAAT"),
        ]
        code, receipt, _ = self.run_checker(left, right)
        self.assertEqual(code, 1)
        assert receipt is not None
        self.assertFalse(receipt["equivalent"])
        self.assertFalse(receipt["partition_equivalent"])
        self.assertEqual(receipt["left"]["clusters"], receipt["right"]["clusters"])

    @requires_cluster_integration
    def test_frequency_change_fails(self) -> None:
        left = [
            sam_record("a1_AAAA", mi=0),
            sam_record("a2_AAAA", mi=0),
            sam_record("b_AAAT", mi=0),
        ]
        right = [
            sam_record("a1_AAAA", mi=7),
            sam_record("b1_AAAT", mi=7),
            sam_record("b2_AAAT", mi=7),
        ]
        code, receipt, _ = self.run_checker(left, right)
        self.assertEqual(code, 1)
        assert receipt is not None
        self.assertFalse(receipt["partition_equivalent"])
        self.assertEqual(receipt["left"]["eligible_records"], 3)
        self.assertEqual(receipt["right"]["eligible_records"], 3)

    @requires_cluster_integration
    def test_split_umi_and_invalid_rx_fail_as_contract_errors(self) -> None:
        split_left = [
            sam_record("a_AAAA", mi=0, rx="AAAA"),
            sam_record("b_AAAA", mi=1, rx="AAAA"),
        ]
        valid_right = [
            sam_record("a_AAAA", mi=7, rx="AAAA"),
            sam_record("b_AAAA", mi=7, rx="AAAA"),
        ]
        code, receipt, completed = self.run_checker(split_left, valid_right)
        self.assertEqual(code, 2)
        self.assertIsNone(receipt)
        self.assertIn("split across multiple MI clusters", completed.stderr)

        invalid_root = [
            sam_record("a_AAAA", mi=0, rx="CCCC"),
        ]
        code, receipt, completed = self.run_checker(
            invalid_root,
            valid_right[:1],
            receipt_name="invalid-rx.json",
        )
        self.assertEqual(code, 2)
        self.assertIsNone(receipt)
        self.assertIn("RX root absent", completed.stderr)

    @requires_cluster_integration
    def test_paired_mode_uses_first_nonsecond_and_tlen_key(self) -> None:
        left = [
            sam_record(
                "pair-left_AAAA",
                flag=99,
                mate_reference="=",
                mate_pos=150,
                tlen=100,
                mi=0,
            ),
            sam_record(
                "ignored-left_AAAA",
                flag=147,
                pos=150,
                mate_reference="=",
                mate_pos=100,
                tlen=-100,
                mi=None,
            ),
        ]
        right = [
            sam_record(
                "ignored-right_CCCC",
                flag=147,
                pos=900,
                mate_reference="=",
                mate_pos=1,
                tlen=-999,
                mi=None,
            ),
            sam_record(
                "pair-right_AAAA",
                flag=99,
                mate_reference="=",
                mate_pos=150,
                tlen=100,
                mi=83,
            ),
        ]
        code, receipt, _ = self.run_checker(
            left, right, extra_arguments=["--mode", "paired"]
        )
        self.assertEqual(code, 0)
        assert receipt is not None
        self.assertEqual(receipt["left"]["eligible_records"], 1)
        self.assertEqual(receipt["left"]["excluded_second_of_pair"], 1)
        self.assertEqual(receipt["right"]["excluded_second_of_pair"], 1)

        # TLEN is part of PairedAlignment, even when all UMI membership agrees.
        right[1] = sam_record(
            "pair-right_AAAA",
            flag=99,
            mate_reference="=",
            mate_pos=150,
            tlen=101,
            mi=83,
        )
        code, receipt, _ = self.run_checker(
            left,
            right,
            extra_arguments=["--mode", "paired"],
            receipt_name="tlen-mismatch.json",
        )
        self.assertEqual(code, 1)
        assert receipt is not None
        self.assertFalse(receipt["partition_equivalent"])

    @requires_cluster_integration
    def test_unclipped_coordinates_match_htsjdk_grouping(self) -> None:
        left = [
            sam_record("forward_AAAA", pos=100, cigar="5S45M", mi=0),
            sam_record("reverse_CCCC", flag=16, pos=200, cigar="45M5S", mi=1, rx="CCCC"),
            sam_record(
                "deletion_GGGG",
                flag=16,
                pos=300,
                cigar="25M5D25M",
                mi=2,
                rx="GGGG",
            ),
        ]
        right = [
            sam_record("deletion_GGGG", flag=16, pos=300, cigar="55M", mi=92, rx="GGGG", read_length=55),
            sam_record("reverse_CCCC", flag=16, pos=200, cigar="50M", mi=91, rx="CCCC"),
            sam_record("forward_AAAA", pos=95, cigar="50M", mi=90),
        ]
        code, receipt, _ = self.run_checker(left, right)
        self.assertEqual(code, 0)
        assert receipt is not None
        self.assertEqual(receipt["left"]["alignment_groups"], 3)

    @requires_cluster_integration
    def test_paired_eligibility_filters_before_requiring_tags(self) -> None:
        header = (
            "@HD\tVN:1.6\tSO:unsorted\n"
            "@SQ\tSN:chr1\tLN:100000\n"
            "@SQ\tSN:chr2\tLN:100000\n"
        )
        left = [
            # Paired with neither first nor second flag: Java retains it.
            sam_record(
                "eligible_AAAA",
                flag=1,
                mate_reference="=",
                mate_pos=150,
                tlen=100,
                mi=3,
            ),
            # All three are filtered before MI/RX are inspected.
            sam_record("unpaired_CCCC", flag=0, mi=None, rx="CCCC"),
            sam_record(
                "mate-unmapped_GGGG",
                flag=73,
                mate_reference="*",
                mi=None,
                rx="GGGG",
            ),
            sam_record(
                "chimeric_TTTT",
                flag=65,
                mate_reference="chr2",
                mi=None,
                rx="TTTT",
            ),
        ]
        right = list(reversed(left))
        code, receipt, _ = self.run_checker(
            left,
            right,
            left_header=header,
            right_header=header,
            extra_arguments=[
                "--mode",
                "paired",
                "--remove-unpaired",
                "--remove-chimeric",
            ],
        )
        self.assertEqual(code, 0)
        assert receipt is not None
        self.assertEqual(receipt["left"]["eligible_records"], 1)
        self.assertEqual(receipt["left"]["excluded_unpaired"], 1)
        self.assertEqual(receipt["left"]["excluded_mate_unmapped"], 1)
        self.assertEqual(receipt["left"]["excluded_chimeric"], 1)

    @requires_cluster_integration
    def test_missing_wrong_type_and_duplicate_cluster_tags_fail_closed(self) -> None:
        valid = [sam_record("valid_AAAA", mi=8)]
        malformed_cases = [
            sam_record("missing_AAAA", mi=None),
            sam_record("wrong-type_AAAA", mi=0).replace("MI:Z:0", "MI:i:0"),
            sam_record("duplicate_AAAA", mi=0, extra="MI:Z:1"),
            sam_record("duplicate-rx_AAAA", mi=0, extra="RX:Z:AAAA"),
        ]
        for index, malformed in enumerate(malformed_cases):
            with self.subTest(index=index):
                code, receipt, _ = self.run_checker(
                    [malformed],
                    valid,
                    receipt_name=f"malformed-{index}.json",
                )
                self.assertEqual(code, 2)
                self.assertIsNone(receipt)

    @requires_cluster_integration
    def test_reference_and_read_group_dictionaries_are_validated(self) -> None:
        records = [sam_record("same_AAAA", mi=0)]
        changed_rg = PRIVATE_HEADER.replace("private-sample", "other-private-sample")
        code, receipt, _ = self.run_checker(
            records, records, right_header=changed_rg
        )
        self.assertEqual(code, 1)
        assert receipt is not None
        self.assertTrue(receipt["partition_equivalent"])
        self.assertFalse(receipt["read_group_dictionary_equivalent"])

        changed_sq = PRIVATE_HEADER.replace("LN:100000", "LN:99999")
        code, receipt, _ = self.run_checker(
            records,
            records,
            right_header=changed_sq,
            receipt_name="sq-mismatch.json",
        )
        self.assertEqual(code, 1)
        assert receipt is not None
        self.assertFalse(receipt["reference_dictionary_equivalent"])

    @requires_cluster_integration
    def test_receipt_contains_no_private_qname_header_or_path_strings(self) -> None:
        private_marker = "never-publish-customer-42"
        header = PRIVATE_HEADER.replace("private-sample", private_marker)
        records_left = [sam_record(f"{private_marker}_AAAA", mi=0)]
        records_right = [sam_record(f"different-prefix-{private_marker}_AAAA", mi=71)]
        code, receipt, completed = self.run_checker(
            records_left, records_right, left_header=header, right_header=header
        )
        self.assertEqual(code, 0)
        assert receipt is not None
        serialized = json.dumps(receipt, sort_keys=True)
        combined = serialized + completed.stdout + completed.stderr
        self.assertNotIn(private_marker, combined)
        self.assertNotIn("private-left.sam", combined)
        self.assertNotIn("private-right.sam", combined)
        receipt_path = self.directory / "receipt.json"
        self.assertEqual(stat.S_IMODE(receipt_path.stat().st_mode), 0o600)

    @requires_cluster_integration
    def test_hardlinked_inputs_and_receipt_collision_fail_closed(self) -> None:
        left = self.directory / "one.sam"
        right = self.directory / "two.sam"
        receipt = self.directory / "hardlink-receipt.json"
        write_sam(left, [sam_record("private_AAAA")])
        os.link(left, right)
        code = CHECKER.main(
            [
                os.fspath(left),
                os.fspath(right),
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

        right.unlink()
        write_sam(right, [sam_record("other_AAAA", mi=8)])
        sentinel = self.directory / "sentinel"
        sentinel.write_text("do-not-overwrite", encoding="ascii")
        os.link(sentinel, receipt)
        code = CHECKER.main(
            [
                os.fspath(left),
                os.fspath(right),
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
        self.assertEqual(sentinel.read_text(encoding="ascii"), "do-not-overwrite")
        self.assertEqual(receipt.read_text(encoding="ascii"), "do-not-overwrite")

    @requires_cluster_integration
    def test_literal_separator_and_umi_truncation_match_samread(self) -> None:
        left = [sam_record("prefix.AAAAT-suffix", mi=0)]
        right = [sam_record("unrelated.AAAAextra", mi=91)]
        code, receipt, _ = self.run_checker(
            left,
            right,
            extra_arguments=["--umi-separator", "."],
        )
        self.assertEqual(code, 0)
        assert receipt is not None
        self.assertTrue(receipt["equivalent"])

    def test_private_workspace_permissions_and_cleanup(self) -> None:
        observed: Path | None = None
        with CHECKER.private_workspace(self.directory) as workspace:
            observed = workspace
            self.assertEqual(stat.S_IMODE(workspace.stat().st_mode), 0o700)
            private_file = workspace / "private"
            with CHECKER.secure_binary_output(private_file) as stream:
                stream.write(b"sensitive")
            self.assertEqual(stat.S_IMODE(private_file.stat().st_mode), 0o600)
        assert observed is not None
        self.assertFalse(observed.exists())

    @unittest.skipUnless(
        hasattr(signal, "SIGTERM") and hasattr(signal, "SIGHUP"),
        "POSIX termination signals are required",
    )
    def test_termination_signals_unwind_and_remove_private_workspace(self) -> None:
        child_program = """
import importlib.util
import os
from pathlib import Path
import signal
import sys

checker_path = Path(sys.argv[1])
temporary_parent = Path(sys.argv[2])
specification = importlib.util.spec_from_file_location(
    "cluster_partition_check_signal_child", checker_path
)
assert specification is not None and specification.loader is not None
checker = importlib.util.module_from_spec(specification)
sys.modules[specification.name] = checker
specification.loader.exec_module(checker)
os.umask(0o077)

def exercise():
    with checker.private_workspace(temporary_parent) as workspace:
        print(workspace, flush=True)
        signal.pause()
    return 0

checker.install_termination_signal_handlers()
try:
    raise SystemExit(exercise())
except checker.PartitionSignalInterrupt as error:
    raise SystemExit(128 + error.signal_number)
"""
        for signal_number in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=signal.Signals(signal_number).name):
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        child_program,
                        os.fspath(CHECKER_PATH),
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
                    self.assertEqual(process.returncode, 128 + signal_number)
                    self.assertFalse(workspace.exists())
                    self.assertEqual(
                        list(self.directory.glob(".dumi-cluster-partition-*")),
                        [],
                    )
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                    if process.stdout is not None:
                        process.stdout.close()
                    if process.stderr is not None:
                        process.stderr.close()

    @requires_cluster_integration
    def test_sort_failure_leaves_no_private_workspace(self) -> None:
        failing_sort = self.directory / "failing-sort"
        failing_sort.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"--version\" ]; then\n"
            "  echo 'sort (GNU coreutils) 9.5'\n"
            "  exit 0\n"
            "fi\n"
            "exit 19\n",
            encoding="ascii",
        )
        failing_sort.chmod(0o700)
        left = self.directory / "left.sam"
        right = self.directory / "right.sam"
        receipt = self.directory / "failed.json"
        write_sam(left, [sam_record("left_AAAA", mi=0)])
        write_sam(right, [sam_record("right_AAAA", mi=1)])
        code = CHECKER.main(
            [
                os.fspath(left),
                os.fspath(right),
                "--receipt",
                os.fspath(receipt),
                "--umi-length",
                "4",
                "--tmpdir",
                os.fspath(self.directory),
                "--sort-command",
                os.fspath(failing_sort),
                "--sort-buffer-size",
                "1M",
            ]
        )
        self.assertEqual(code, 2)
        self.assertFalse(receipt.exists())
        self.assertEqual(
            list(self.directory.glob(".dumi-cluster-partition-*")),
            [],
        )


if __name__ == "__main__":
    unittest.main()
