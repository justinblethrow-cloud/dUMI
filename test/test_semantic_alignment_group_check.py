#!/usr/bin/env python3
"""Focused tests for exact headers and alignment-group count fingerprints."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "benchmark" / "semantic_check.py"


def sam_text(
    records: list[str],
    *,
    sequence_dictionary: str = "@SQ\tSN:chr1\tLN:1000",
    read_group: str = "@RG\tID:rg1\tSM:sample",
    sort_order: str = "coordinate",
    program: str = "@PG\tID:fixture\tPN:fixture\tVN:1",
) -> str:
    header = (
        f"@HD\tVN:1.6\tSO:{sort_order}\n"
        f"{sequence_dictionary}\n"
        f"{read_group}\n"
        f"{program}\n"
    )
    return header + "\n".join(records) + "\n"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


SINGLE_RECORD = (
    "readA_AAAA\t0\tchr1\t100\t60\t10M\t*\t0\t0\t"
    "AAAAAAAAAA\tIIIIIIIIII\tRG:Z:rg1"
)
PAIRED_FIRST = (
    "pairA_AAAA\t99\tchr1\t100\t60\t10M\t=\t150\t60\t"
    "AAAAAAAAAA\tIIIIIIIIII\tRG:Z:rg1"
)
PAIRED_SECOND = (
    "pairA_AAAA\t147\tchr1\t150\t60\t10M\t=\t100\t-60\t"
    "CCCCCCCCCC\tIIIIIIIIII\tRG:Z:rg1"
)


class SemanticAlignmentGroupCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="dumi-semantic-alignment-group-test."
        )
        self.root = Path(self.temporary.name)
        samtools = shutil.which("samtools")
        self.real_samtools = samtools is not None
        if samtools is None:
            fake_samtools = self.root / "samtools"
            fake_samtools.write_text(
                """#!/usr/bin/env python3
import pathlib
import sys

arguments = sys.argv[1:]
if len(arguments) >= 3 and arguments[0] == "quickcheck" and arguments[1] == "-v":
    path = pathlib.Path(arguments[2])
    sys.exit(0 if path.is_file() else 1)
if len(arguments) >= 2 and arguments[0] == "view":
    header_only = arguments[1] == "-H"
    path = pathlib.Path(arguments[2] if header_only else arguments[1])
    with path.open("rb") as stream:
        for line in stream:
            is_header = line.startswith(b"@")
            if is_header == header_only:
                sys.stdout.buffer.write(line)
    sys.exit(0)
sys.stderr.write("unsupported fake-samtools invocation\\n")
sys.exit(2)
""",
                encoding="utf-8",
            )
            fake_samtools.chmod(0o700)
            samtools = str(fake_samtools)
        self.samtools = str(Path(samtools).resolve())

    def tearDown(self) -> None:
        if hasattr(self, "temporary"):
            self.temporary.cleanup()

    def bam(self, name: str, text: str) -> Path:
        sam = self.root / f"{name}.sam"
        sam.write_text(text, encoding="ascii")
        if not self.real_samtools:
            return sam
        bam = self.root / f"{name}.bam"
        subprocess.run(
            [self.samtools, "view", "-b", "-o", bam, sam],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return bam

    def check(
        self,
        output: Path,
        *,
        reference: Path | None = None,
        alignment_group_mode: str = "single-end",
        extra: list[object] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command: list[object] = [
            sys.executable,
            CHECKER,
            "--samtools",
            self.samtools,
            "--alignment-group-mode",
            alignment_group_mode,
        ]
        if reference is not None:
            command.extend(["--reference", reference])
        if extra:
            command.extend(extra)
        command.append(output)
        return subprocess.run(
            [str(value) for value in command],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_changed_ordered_read_group_is_rejected(self) -> None:
        reference = self.bam("reference", sam_text([SINGLE_RECORD]))
        candidate = self.bam(
            "candidate",
            sam_text(
                [SINGLE_RECORD],
                read_group="@RG\tID:rg1\tSM:different-sample",
            ),
        )

        completed = self.check(candidate, reference=reference)
        self.assertEqual(completed.returncode, 1, completed.stderr)
        receipt = json.loads(completed.stdout)
        self.assertTrue(receipt["record_equivalent"])
        self.assertTrue(receipt["reference_dictionary_equivalent"])
        self.assertFalse(receipt["read_group_dictionary_equivalent"])
        self.assertTrue(receipt["alignment_group_output_count_equivalent"])
        self.assertEqual(receipt["read_groups"], 1)
        self.assertEqual(receipt["expected_read_groups"], 1)
        self.assertNotEqual(
            receipt["read_group_dictionary_sha256"],
            receipt["expected_read_group_dictionary_sha256"],
        )
        self.assertNotEqual(
            receipt["read_group_dictionary_sha256"],
            "0" * 64,
        )
        self.assertNotIn("different-sample", completed.stdout)
        self.assertNotIn("readA_AAAA", completed.stdout)

    def test_reordered_read_groups_are_rejected(self) -> None:
        rg1 = "@RG\tID:rg1\tSM:sample-one"
        rg2 = "@RG\tID:rg2\tSM:sample-two"
        reference = self.bam(
            "reference",
            sam_text([SINGLE_RECORD], read_group=f"{rg1}\n{rg2}"),
        )
        candidate = self.bam(
            "candidate",
            sam_text([SINGLE_RECORD], read_group=f"{rg2}\n{rg1}"),
        )

        completed = self.check(candidate, reference=reference)
        self.assertEqual(completed.returncode, 1, completed.stderr)
        receipt = json.loads(completed.stdout)
        self.assertTrue(receipt["record_equivalent"])
        self.assertFalse(receipt["read_group_dictionary_equivalent"])
        self.assertEqual(receipt["read_groups"], 2)
        self.assertEqual(receipt["expected_read_groups"], 2)
        self.assertNotIn("sample-one", completed.stdout)
        self.assertNotIn("sample-two", completed.stdout)

    def test_reordered_reference_sequences_are_rejected(self) -> None:
        sq1 = "@SQ\tSN:chr1\tLN:1000"
        sq2 = "@SQ\tSN:chr2\tLN:2000"
        reference = self.bam(
            "reference",
            sam_text(
                [SINGLE_RECORD],
                sequence_dictionary=f"{sq1}\n{sq2}",
            ),
        )
        candidate = self.bam(
            "candidate",
            sam_text(
                [SINGLE_RECORD],
                sequence_dictionary=f"{sq2}\n{sq1}",
            ),
        )

        completed = self.check(candidate, reference=reference)
        self.assertEqual(completed.returncode, 1, completed.stderr)
        receipt = json.loads(completed.stdout)
        self.assertTrue(receipt["record_equivalent"])
        self.assertFalse(receipt["reference_dictionary_equivalent"])
        self.assertEqual(receipt["reference_sequences"], 2)
        self.assertEqual(receipt["expected_reference_sequences"], 2)
        self.assertNotIn("@SQ", completed.stdout)

    def test_hd_and_pg_differences_are_intentionally_ignored(self) -> None:
        reference = self.bam(
            "reference",
            sam_text(
                [SINGLE_RECORD],
                sort_order="coordinate",
                program="@PG\tID:reference-program\tPN:reference",
            ),
        )
        candidate = self.bam(
            "candidate",
            sam_text(
                [SINGLE_RECORD],
                sort_order="unknown",
                program="@PG\tID:candidate-program\tPN:candidate",
            ),
        )

        completed = self.check(candidate, reference=reference)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        receipt = json.loads(completed.stdout)
        self.assertTrue(receipt["record_equivalent"])
        self.assertTrue(receipt["reference_dictionary_equivalent"])
        self.assertTrue(receipt["read_group_dictionary_equivalent"])
        self.assertTrue(receipt["alignment_group_output_count_equivalent"])
        self.assertEqual(receipt["sort_order"], "unknown")

    def test_different_records_can_match_only_the_output_count_gate(self) -> None:
        reference_record = (
            "tieA_AAAA\t0\tchr1\t103\t60\t3S7M\t*\t0\t0\t"
            "AAACCCCCCC\tIIIIIIIIII\tRG:Z:rg1"
        )
        candidate_record = (
            "tieB_CCCC\t0\tchr1\t100\t20\t10M\t*\t0\t0\t"
            "GGGGGGGGGG\tJJJJJJJJJJ\tRG:Z:rg1"
        )
        reference_reverse = (
            "tieR_AAAA\t16\tchr1\t100\t60\t7M3S\t*\t0\t0\t"
            "CCCCCCCAAA\tIIIIIIIIII\tRG:Z:rg1"
        )
        candidate_reverse = (
            "tieS_CCCC\t16\tchr1\t100\t20\t10M\t*\t0\t0\t"
            "TTTTTTTTTT\tJJJJJJJJJJ\tRG:Z:rg1"
        )
        reference = self.bam(
            "reference",
            sam_text([reference_reverse, reference_record]),
        )
        candidate = self.bam(
            "candidate",
            sam_text([candidate_record, candidate_reverse]),
        )

        completed = self.check(candidate, reference=reference)
        self.assertEqual(completed.returncode, 1, completed.stderr)
        receipt = json.loads(completed.stdout)
        self.assertFalse(receipt["record_equivalent"])
        self.assertNotEqual(
            receipt["semantic_sha256"],
            receipt["reference_canonical_sha256"],
        )
        self.assertTrue(receipt["alignment_group_output_count_equivalent"])
        self.assertEqual(
            receipt["alignment_group_fingerprint_version"],
            "dumi-umicollapse-alignment-group-output-count-v1",
        )
        self.assertEqual(receipt["alignment_group_output_records"], 2)
        self.assertEqual(
            receipt["reference_alignment_group_output_records"], 2
        )
        self.assertEqual(
            receipt["alignment_group_output_count_sha256"],
            receipt["reference_alignment_group_output_count_sha256"],
        )
        self.assertNotIn("tieA_AAAA", completed.stdout)
        self.assertNotIn("tieB_CCCC", completed.stdout)

    def test_duplicate_group_multiplicity_detects_added_molecule(self) -> None:
        reference = self.bam("reference", sam_text([SINGLE_RECORD]))
        second = SINGLE_RECORD.replace("readA_AAAA", "readB_CCCC")
        candidate = self.bam(
            "candidate",
            sam_text([SINGLE_RECORD, second]),
        )

        completed = self.check(candidate, reference=reference)
        self.assertEqual(completed.returncode, 1, completed.stderr)
        receipt = json.loads(completed.stdout)
        self.assertFalse(receipt["record_equivalent"])
        self.assertFalse(receipt["alignment_group_output_count_equivalent"])
        self.assertEqual(receipt["alignment_group_output_records"], 2)
        self.assertEqual(
            receipt["reference_alignment_group_output_records"], 1
        )
        self.assertNotEqual(
            receipt["alignment_group_output_count_sha256"],
            receipt["reference_alignment_group_output_count_sha256"],
        )

    def test_paired_mode_uses_first_record_and_template_length(self) -> None:
        reference = self.bam(
            "reference",
            sam_text([PAIRED_FIRST, PAIRED_SECOND]),
        )
        alternate_second = PAIRED_SECOND.replace(
            "CCCCCCCCCC", "GGGGGGGGGG"
        )
        representative_tie = self.bam(
            "representative-tie",
            sam_text([PAIRED_FIRST, alternate_second]),
        )
        tie_result = self.check(
            representative_tie,
            reference=reference,
            alignment_group_mode="paired",
        )
        self.assertEqual(tie_result.returncode, 1, tie_result.stderr)
        tie_receipt = json.loads(tie_result.stdout)
        self.assertFalse(tie_receipt["record_equivalent"])
        self.assertTrue(
            tie_receipt["alignment_group_output_count_equivalent"]
        )
        self.assertEqual(tie_receipt["alignment_group_output_records"], 1)
        self.assertEqual(
            tie_receipt[
                "alignment_group_records_excluded_second_of_pair"
            ],
            1,
        )

        changed_tlen = PAIRED_FIRST.replace("\t60\tAAAAAAAAAA", "\t61\tAAAAAAAAAA")
        tlen_candidate = self.bam(
            "changed-tlen",
            sam_text([changed_tlen, PAIRED_SECOND]),
        )
        tlen_result = self.check(
            tlen_candidate,
            reference=reference,
            alignment_group_mode="paired",
        )
        self.assertEqual(tlen_result.returncode, 1, tlen_result.stderr)
        self.assertFalse(
            json.loads(tlen_result.stdout)[
                "alignment_group_output_count_equivalent"
            ]
        )

    def test_cached_reference_can_be_bound_and_tampering_fails_closed(self) -> None:
        reference = self.bam("reference", sam_text([SINGLE_RECORD]))
        canonical = self.root / "reference.records.sorted.private"
        cache_receipt = self.root / "reference.cache-receipt.json"
        generated = self.check(
            reference,
            extra=[
                "--canonical-output",
                canonical,
                "--canonical-receipt-output",
                cache_receipt,
            ],
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)
        expected_sha256 = json.loads(generated.stdout)["semantic_sha256"]
        self.assertEqual(stat.S_IMODE(canonical.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(cache_receipt.stat().st_mode), 0o600)
        cache_payload = json.loads(cache_receipt.read_text(encoding="utf-8"))
        self.assertEqual(
            cache_payload["source_file_sha256"],
            sha256(reference),
        )
        self.assertNotIn(str(reference), cache_receipt.read_text(encoding="utf-8"))
        self.assertNotIn("readA_AAAA", cache_receipt.read_text(encoding="utf-8"))

        verified = self.check(
            reference,
            reference=reference,
            extra=[
                "--reference-canonical",
                canonical,
                "--reference-canonical-receipt",
                cache_receipt,
                "--reference-canonical-sha256",
                expected_sha256,
            ],
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        verified_receipt = json.loads(verified.stdout)
        self.assertTrue(
            verified_receipt["reference_canonical_sha256_verified"]
        )
        self.assertTrue(
            verified_receipt["reference_cache_receipt_verified"]
        )
        self.assertTrue(verified_receipt["record_equivalent"])
        self.assertTrue(
            verified_receipt["alignment_group_output_count_equivalent"]
        )

        with canonical.open("ab") as stream:
            stream.write(b"\n")
        tampered = self.check(
            reference,
            reference=reference,
            extra=[
                "--reference-canonical",
                canonical,
                "--reference-canonical-receipt",
                cache_receipt,
                "--reference-canonical-sha256",
                expected_sha256,
            ],
        )
        self.assertEqual(tampered.returncode, 2)
        self.assertEqual(tampered.stdout, "")
        self.assertIn(
            "reference canonical stream does not match",
            tampered.stderr,
        )
        self.assertNotIn("readA_AAAA", tampered.stderr)

    def test_cache_receipt_rejects_wrong_reference_provenance(self) -> None:
        reference_a = self.bam("reference-a", sam_text([SINGLE_RECORD]))
        record_b = SINGLE_RECORD.replace("readA_AAAA", "readB_CCCC")
        reference_b = self.bam("reference-b", sam_text([record_b]))
        canonical_b = self.root / "reference-b.records.sorted.private"
        receipt_b = self.root / "reference-b.cache-receipt.json"
        generated = self.check(
            reference_b,
            extra=[
                "--canonical-output",
                canonical_b,
                "--canonical-receipt-output",
                receipt_b,
            ],
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)

        wrong_reference = self.check(
            reference_b,
            reference=reference_a,
            extra=[
                "--reference-canonical",
                canonical_b,
                "--reference-canonical-receipt",
                receipt_b,
            ],
        )
        self.assertEqual(wrong_reference.returncode, 2)
        self.assertEqual(wrong_reference.stdout, "")
        self.assertIn(
            "reference file SHA-256 does not match",
            wrong_reference.stderr,
        )
        self.assertNotIn("readA_AAAA", wrong_reference.stderr)
        self.assertNotIn("readB_CCCC", wrong_reference.stderr)

    def test_cached_reference_reuses_alignment_group_count_signature(self) -> None:
        reference = self.bam("reference", sam_text([SINGLE_RECORD]))
        canonical = self.root / "reference.records.sorted.private"
        cache_receipt = self.root / "reference.cache-receipt.json"
        generated = self.check(
            reference,
            extra=[
                "--canonical-output",
                canonical,
                "--canonical-receipt-output",
                cache_receipt,
            ],
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)

        real_sort = shutil.which("sort")
        self.assertIsNotNone(real_sort)
        counter = self.root / "sort-invocations.txt"
        wrapper = self.root / "counting-sort"
        wrapper.write_text(
            "#!/usr/bin/env python3\n"
            "import os\n"
            "import pathlib\n"
            "import sys\n"
            f"counter = pathlib.Path({str(counter)!r})\n"
            "with counter.open('a', encoding='ascii') as stream:\n"
            "    stream.write('sort\\n')\n"
            f"real_sort = {str(real_sort)!r}\n"
            "os.execv(real_sort, [real_sort, *sys.argv[1:]])\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o700)

        verified = self.check(
            reference,
            reference=reference,
            extra=[
                "--reference-canonical",
                canonical,
                "--reference-canonical-receipt",
                cache_receipt,
                "--sort-command",
                wrapper,
            ],
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(
            counter.read_text(encoding="ascii").splitlines(),
            ["sort"],
        )
        verified_payload = json.loads(verified.stdout)
        self.assertTrue(
            verified_payload[
                "alignment_group_output_count_reused_from_exact_reference"
            ]
        )

        counter.write_text("", encoding="ascii")
        changed_record = SINGLE_RECORD.replace("readA_AAAA", "readB_CCCC")
        changed = self.bam("changed", sam_text([changed_record]))
        different = self.check(
            changed,
            reference=reference,
            extra=[
                "--reference-canonical",
                canonical,
                "--reference-canonical-receipt",
                cache_receipt,
                "--sort-command",
                wrapper,
            ],
        )
        self.assertEqual(different.returncode, 1, different.stderr)
        self.assertEqual(
            counter.read_text(encoding="ascii").splitlines(),
            ["sort", "sort"],
        )
        different_payload = json.loads(different.stdout)
        self.assertFalse(different_payload["record_equivalent"])
        self.assertFalse(
            different_payload[
                "alignment_group_output_count_reused_from_exact_reference"
            ]
        )
        self.assertTrue(
            different_payload["alignment_group_output_count_equivalent"]
        )

    def test_private_atomic_outputs_have_restrictive_modes(self) -> None:
        reference = self.bam("reference", sam_text([SINGLE_RECORD]))
        canonical = self.root / "private.records.sorted"
        cache_receipt = self.root / "private.cache-receipt.json"
        report = self.root / "new-private-directory" / "report.json"
        original_umask = os.umask(0o002)
        try:
            completed = self.check(
                reference,
                extra=[
                    "--canonical-output",
                    canonical,
                    "--canonical-receipt-output",
                    cache_receipt,
                    "--report",
                    report,
                ],
            )
        finally:
            os.umask(original_umask)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "")
        for path in (canonical, cache_receipt, report):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(
            stat.S_IMODE(report.parent.stat().st_mode),
            0o700,
        )
        report_payload = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(report_payload["quickcheck_status"], "pass")

    def test_hardlink_destinations_cannot_overwrite_inputs_or_caches(self) -> None:
        inspected = self.bam("inspected", sam_text([SINGLE_RECORD]))
        original_digest = sha256(inspected)

        report_alias = self.root / "report-alias.json"
        os.link(inspected, report_alias)
        report_result = self.check(
            inspected,
            extra=["--report", report_alias],
        )
        self.assertEqual(report_result.returncode, 2)
        self.assertEqual(sha256(inspected), original_digest)
        self.assertIn("must not overwrite or alias", report_result.stderr)

        canonical_alias = self.root / "canonical-alias.private"
        os.link(inspected, canonical_alias)
        cache_receipt = self.root / "cache-receipt.json"
        canonical_result = self.check(
            inspected,
            extra=[
                "--canonical-output",
                canonical_alias,
                "--canonical-receipt-output",
                cache_receipt,
            ],
        )
        self.assertEqual(canonical_result.returncode, 2)
        self.assertEqual(sha256(inspected), original_digest)

        safe_canonical = self.root / "safe.records.sorted.private"
        safe_receipt = self.root / "safe.cache-receipt.json"
        generated = self.check(
            inspected,
            extra=[
                "--canonical-output",
                safe_canonical,
                "--canonical-receipt-output",
                safe_receipt,
            ],
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)
        cache_digest = sha256(safe_canonical)
        cache_report_alias = self.root / "cache-report-alias.json"
        os.link(safe_canonical, cache_report_alias)
        cached_result = self.check(
            inspected,
            reference=inspected,
            extra=[
                "--reference-canonical",
                safe_canonical,
                "--reference-canonical-receipt",
                safe_receipt,
                "--report",
                cache_report_alias,
            ],
        )
        self.assertEqual(cached_result.returncode, 2)
        self.assertEqual(sha256(safe_canonical), cache_digest)


if __name__ == "__main__":
    unittest.main()
