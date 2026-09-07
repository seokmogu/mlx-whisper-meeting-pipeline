from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unicodedata
import unittest


REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "sh" / "evaluate_transcript_quality.py"


class EvaluateTranscriptQualityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.manifest = self.root / "manifest.json"
        self.output = self.root / "report.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write(self, name: str, content: str) -> Path:
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        return path

    def _case(self, case_id: str, reference: str | None, hypothesis: str | None, **extra: object) -> dict:
        case: dict[str, object] = {
            "id": case_id,
            "reviewed": True,
            "reviewer": "synthetic-reviewer",
            "evidence": "synthetic-evidence",
        }
        if reference is not None:
            case["reference"] = reference
        if hypothesis is not None:
            case["hypothesis"] = hypothesis
        case.update(extra)
        return case

    def _run(self, manifest: dict, *, output: Path | None = None) -> subprocess.CompletedProcess[str]:
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(RUNNER), str(self.manifest), "--output", str(output or self.output)],
            text=True,
            capture_output=True,
            check=False,
        )

    def _report(self) -> dict:
        return json.loads(self.output.read_text(encoding="utf-8"))

    def test_known_edit_counts_are_reported_by_hashed_case_id(self) -> None:
        self._write("gold.txt", "one two\n")
        self._write("hyp.txt", "one three\n")
        result = self._run({"version": 1, "thresholds": {"cer": 0, "wer": 0}, "cases": [self._case("case-001", "gold.txt", "hyp.txt")]})

        self.assertEqual(1, result.returncode, result.stderr)
        report = self._report()
        item = report["by_case_id_sha256"][hashlib.sha256(b"case-001").hexdigest()]
        self.assertEqual(4, item["metrics"]["cer"]["edit_distance"])
        self.assertEqual(6, item["metrics"]["cer"]["reference_units"])
        self.assertEqual(1, item["metrics"]["wer"]["edit_distance"])
        self.assertEqual(2, item["metrics"]["wer"]["reference_units"])
        self.assertEqual("gated_fail", item["quality_verdict"])
        self.assertEqual(1, report["gated_fail_case_count"])

    def test_korean_nfc_timestamp_and_speaker_prefixes_are_transparent(self) -> None:
        self._write("gold.txt", "[00:01:02] 화자 A: 안 됩니다\n")
        self._write("hyp.txt", "00:01:02 Speaker A: " + unicodedata.normalize("NFD", "안 됩니다") + "\n")
        result = self._run({"version": 1, "cases": [self._case("case-korean", "gold.txt", "hyp.txt")]})

        self.assertEqual(0, result.returncode, result.stderr)
        item = self._report()["by_case_id_sha256"][hashlib.sha256(b"case-korean").hexdigest()]
        self.assertEqual(0, item["metrics"]["cer"]["edit_distance"])
        self.assertEqual(0, item["metrics"]["wer"]["edit_distance"])
        self.assertEqual("not_evaluated", item["speaker_label_check"]["status"])
        self.assertIsNone(item["speaker_label_check"]["matching_count"])
        self.assertEqual("measured_not_gated", item["quality_verdict"])

    def test_insertion_rate_can_exceed_one(self) -> None:
        self._write("gold.txt", "가\n")
        self._write("hyp.txt", "가 가 가\n")
        result = self._run({"version": 1, "cases": [self._case("case-insertions", "gold.txt", "hyp.txt")]})

        self.assertEqual(0, result.returncode, result.stderr)
        item = self._report()["by_case_id_sha256"][hashlib.sha256(b"case-insertions").hexdigest()]
        self.assertEqual(2.0, item["metrics"]["cer"]["rate"])
        self.assertEqual(2.0, item["metrics"]["wer"]["rate"])

    def test_empty_hypothesis_counts_reference_units_as_deletions(self) -> None:
        self._write("gold.txt", "가 나\n")
        self._write("hyp.txt", "")
        result = self._run({"version": 1, "cases": [self._case("case-empty-hypothesis", "gold.txt", "hyp.txt")]})

        self.assertEqual(0, result.returncode, result.stderr)
        item = self._report()["by_case_id_sha256"][hashlib.sha256(b"case-empty-hypothesis").hexdigest()]
        self.assertEqual(2, item["metrics"]["cer"]["edit_distance"])
        self.assertEqual(2, item["metrics"]["wer"]["edit_distance"])
        self.assertEqual(1.0, item["metrics"]["cer"]["rate"])
        self.assertEqual(1.0, item["metrics"]["wer"]["rate"])

    def test_critical_checks_use_complete_token_phrases(self) -> None:
        self._write("gold.txt", "예산은 10 입니다 안 됩니다\n")
        self._write("hyp.txt", "예산은 100 입니다 안녕하세요\n")
        result = self._run(
            {
                "version": 1,
                "cases": [
                    self._case(
                        "case-critical",
                        "gold.txt",
                        "hyp.txt",
                        critical={"numbers": ["10"], "negation": ["안"]},
                    )
                ],
            }
        )

        self.assertEqual(0, result.returncode, result.stderr)
        rows = self._report()["by_case_id_sha256"][hashlib.sha256(b"case-critical").hexdigest()]["critical_expression_check"]["categories"]
        self.assertEqual({"numbers": 1, "negation": 1}, {row["category"]: row["lexically_missing_count"] for row in rows})

    def test_transport_prefixes_are_stripped_without_removing_business_or_malformed_content(self) -> None:
        self._write("range-gold.txt", "[00:01:02 - 00:01:05] A: 결정: 승인\n")
        self._write("range-hyp.txt", "[00:01:02 - 00:01:05] B: 결정: 승인\n")
        range_result = self._run({"version": 1, "cases": [self._case("case-range", "range-gold.txt", "range-hyp.txt")]})
        self.assertEqual(0, range_result.returncode, range_result.stderr)
        range_item = self._report()["by_case_id_sha256"][hashlib.sha256(b"case-range").hexdigest()]
        self.assertEqual(0, range_item["metrics"]["cer"]["edit_distance"])

        self._write("business-gold.txt", "결정: 승인\n")
        self._write("business-hyp.txt", "반려: 승인\n")
        business_result = self._run({"version": 1, "cases": [self._case("case-business", "business-gold.txt", "business-hyp.txt")]})
        self.assertEqual(0, business_result.returncode, business_result.stderr)
        business_item = self._report()["by_case_id_sha256"][hashlib.sha256(b"case-business").hexdigest()]
        self.assertGreater(business_item["metrics"]["cer"]["edit_distance"], 0)

        self._write("malformed-gold.txt", "[00:01:02 - malformed] A: 결정\n")
        self._write("malformed-hyp.txt", "[00:01:02 - malformed] B: 결정\n")
        malformed_result = self._run({"version": 1, "cases": [self._case("case-malformed", "malformed-gold.txt", "malformed-hyp.txt")]})
        self.assertEqual(0, malformed_result.returncode, malformed_result.stderr)
        malformed_item = self._report()["by_case_id_sha256"][hashlib.sha256(b"case-malformed").hexdigest()]
        self.assertGreater(malformed_item["metrics"]["cer"]["edit_distance"], 0)

        self._write("spoken-time-gold.txt", "10:00에 출발\n")
        self._write("spoken-time-hyp.txt", "10:00에 도착\n")
        spoken_time_result = self._run({"version": 1, "cases": [self._case("case-spoken-time", "spoken-time-gold.txt", "spoken-time-hyp.txt")]})
        self.assertEqual(0, spoken_time_result.returncode, spoken_time_result.stderr)
        spoken_time_item = self._report()["by_case_id_sha256"][hashlib.sha256(b"case-spoken-time").hexdigest()]
        self.assertGreater(spoken_time_item["metrics"]["cer"]["edit_distance"], 0)

    def test_missing_reference_and_review_or_thresholds_do_not_claim_a_pass(self) -> None:
        self._write("gold.txt", "가 나\n")
        self._write("hyp.txt", "가 나\n")
        result = self._run(
            {
                "version": 1,
                "cases": [
                    self._case("case-no-reference", None, None),
                    self._case("case-not-reviewed", "gold.txt", "hyp.txt", reviewed=False),
                    self._case("case-not-gated", "gold.txt", "hyp.txt"),
                ],
            }
        )

        self.assertEqual(0, result.returncode, result.stderr)
        cases = self._report()["by_case_id_sha256"]
        self.assertEqual("not_evaluated", cases[hashlib.sha256(b"case-no-reference").hexdigest()]["quality_verdict"])
        self.assertEqual("not_reviewed", cases[hashlib.sha256(b"case-not-reviewed").hexdigest()]["quality_verdict"])
        self.assertEqual("measured_not_gated", cases[hashlib.sha256(b"case-not-gated").hexdigest()]["quality_verdict"])

    def test_rejects_duplicate_ids_and_output_alias_without_changing_sources(self) -> None:
        gold = self._write("gold.txt", "immutable synthetic source\n")
        hypothesis = self._write("hyp.txt", "immutable synthetic source\n")
        source_before = gold.read_text(encoding="utf-8")
        duplicate = self._run(
            {
                "version": 1,
                "cases": [
                    self._case("case-duplicate", "gold.txt", "hyp.txt"),
                    self._case("case-duplicate", "gold.txt", "hyp.txt"),
                ],
            }
        )
        self.assertEqual(2, duplicate.returncode)
        self.assertFalse(self.output.exists())

        aliased = self._run({"version": 1, "cases": [self._case("case-alias", "gold.txt", "hyp.txt")]}, output=gold)
        self.assertEqual(2, aliased.returncode)
        self.assertEqual(source_before, gold.read_text(encoding="utf-8"))

        hypothesis_alias = self.root / "hypothesis-output-alias.json"
        os.link(hypothesis, hypothesis_alias)
        hardlinked = self._run(
            {"version": 1, "cases": [self._case("case-hardlink-alias", "gold.txt", "hyp.txt")]},
            output=hypothesis_alias,
        )
        self.assertEqual(2, hardlinked.returncode)
        self.assertEqual("immutable synthetic source\n", hypothesis.read_text(encoding="utf-8"))

        manifest_alias = self._run({"version": 1, "cases": [self._case("case-manifest-alias", "gold.txt", "hyp.txt")]}, output=self.manifest)
        self.assertEqual(2, manifest_alias.returncode)

    def test_rejects_reference_hypothesis_aliases_including_links_and_cross_case_roles(self) -> None:
        source = self._write("source.txt", "shared synthetic source\n")
        self._write("other-gold.txt", "separate synthetic reference\n")
        self._write("other-hyp.txt", "separate synthetic hypothesis\n")
        source_before = source.read_text(encoding="utf-8")

        for label, hypothesis in (("same", "source.txt"), ("symlink", "source-symlink.txt"), ("hardlink", "source-hardlink.txt")):
            if label == "symlink":
                self.root.joinpath(hypothesis).symlink_to("source.txt")
            elif label == "hardlink":
                os.link(source, self.root / hypothesis)
            result = self._run({"version": 1, "cases": [self._case(f"case-{label}", "source.txt", hypothesis)]})
            self.assertEqual(2, result.returncode)
            self.assertEqual(source_before, source.read_text(encoding="utf-8"))

        cross_case = self._run(
            {
                "version": 1,
                "cases": [
                    self._case("case-first", "source.txt", "other-hyp.txt"),
                    self._case("case-second", "other-gold.txt", "source.txt"),
                ],
            }
        )
        self.assertEqual(2, cross_case.returncode)

    def test_rejects_nonfinite_thresholds(self) -> None:
        self._write("gold.txt", "가\n")
        self._write("hyp.txt", "가\n")
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                result = self._run({"version": 1, "thresholds": {"cer": value}, "cases": [self._case("case-threshold", "gold.txt", "hyp.txt")]})
                self.assertEqual(2, result.returncode)
                self.assertFalse(self.output.exists())

    def test_invalid_source_encoding_returns_a_sanitized_error(self) -> None:
        self.root.joinpath("invalid-source.txt").write_bytes(b"\xff\xfe")
        self._write("hyp.txt", "synthetic hypothesis\n")
        result = self._run({"version": 1, "cases": [self._case("case-invalid-encoding", "invalid-source.txt", "hyp.txt")]})

        self.assertEqual(2, result.returncode)
        self.assertNotIn("invalid-source.txt", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_report_never_leaks_source_text_paths_or_metadata(self) -> None:
        reference_secret = "PRIVATE_REFERENCE_SENTENCE"
        hypothesis_secret = "PRIVATE_HYPOTHESIS_SENTENCE"
        self._write("private-gold.txt", reference_secret)
        self._write("private-hyp.txt", hypothesis_secret)
        result = self._run(
            {
                "version": 1,
                "cases": [
                    self._case(
                        "case-private",
                        "private-gold.txt",
                        "private-hyp.txt",
                        reviewer="PRIVATE_REVIEWER",
                        evidence="PRIVATE_EVIDENCE",
                        critical={"owner": [reference_secret]},
                        speaker_labels=[{"reference": "PRIVATE_LABEL_A", "hypothesis": "PRIVATE_LABEL_A"}],
                    )
                ],
            }
        )

        self.assertEqual(0, result.returncode, result.stderr)
        report_text = self.output.read_text(encoding="utf-8")
        payload = self._report()["by_case_id_sha256"][hashlib.sha256(b"case-private").hexdigest()]
        self.assertEqual(hashlib.sha256(reference_secret.encode("utf-8")).hexdigest(), payload["source_sha256"]["reference"])
        self.assertEqual(hashlib.sha256(hypothesis_secret.encode("utf-8")).hexdigest(), payload["source_sha256"]["hypothesis"])
        for forbidden in (
            reference_secret,
            hypothesis_secret,
            "private-gold.txt",
            "private-hyp.txt",
            "case-private",
            "PRIVATE_REVIEWER",
            "PRIVATE_EVIDENCE",
            "PRIVATE_LABEL_A",
        ):
            self.assertNotIn(forbidden, report_text)


if __name__ == "__main__":
    unittest.main()
