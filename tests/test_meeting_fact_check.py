from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "sh" / "meeting_fact_check.py"
SPEC = importlib.util.spec_from_file_location("meeting_fact_check", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def candidate_payload(*, checkability: str = "public_web", web: bool = True) -> dict:
    return {
        "version": 1,
        "candidates": [
            {
                "id": "C1",
                "speaker": "A",
                "start_seconds": 10.0,
                "end_seconds": 14.0,
                "claim_text": "1 더하기 1은 3이다.",
                "public_claim": "In standard arithmetic, 1 + 1 equals 3.",
                "required_evidence": "",
                "checkability": checkability,
                "importance": "high",
                "stt_risk": False,
                "web_search_allowed": web,
                "counterpart_claims": [],
            }
        ],
    }


def evidence_payload(*, url: str = "https://example.org/arithmetic") -> dict:
    return {
        "version": 1,
        "web_search_performed": True,
        "results": [
            {
                "candidate_id": "C1",
                "verdict": "contradicted",
                "corrected_fact": "표준 산술에서 1 + 1은 2다.",
                "explanation": "자연수 덧셈 규칙과 계산 결과가 3이 아니라 2이기 때문이다.",
                "error_type": "unit_or_calculation",
                "confidence": "high",
                "valid_as_of": "일반 산술",
                "sources": [
                    {
                        "title": "Arithmetic rule",
                        "url": url,
                        "source_type": "standard",
                        "locator": "Addition definition",
                        "why_relevant": "1과 1의 덧셈 결과를 정의한다.",
                    }
                ],
            }
        ],
    }


class MeetingFactCheckTest(unittest.TestCase):
    def test_valid_candidate_and_evidence_pass(self) -> None:
        candidates = candidate_payload()
        self.assertEqual([], MODULE.validate_candidates(candidates))
        self.assertEqual([], MODULE.validate_evidence(evidence_payload(), {"C1"}))

    def test_internal_claim_cannot_be_sent_to_public_web(self) -> None:
        payload = candidate_payload(checkability="internal_authority", web=True)
        payload["candidates"][0]["required_evidence"] = "승인된 시스템 설정"
        errors = MODULE.validate_candidates(payload)
        self.assertTrue(any("cannot use public web search" in error for error in errors))

    def test_search_result_url_is_not_direct_evidence(self) -> None:
        errors = MODULE.validate_evidence(
            evidence_payload(url="https://www.google.com/search?q=1%2B1"),
            {"C1"},
        )
        self.assertTrue(any("direct HTTPS URL" in error for error in errors))

    def test_contradiction_requires_high_confidence_and_correction(self) -> None:
        payload = evidence_payload()
        payload["results"][0]["confidence"] = "medium"
        payload["results"][0]["corrected_fact"] = ""
        errors = MODULE.validate_evidence(payload, {"C1"})
        self.assertTrue(any("high confidence" in error for error in errors))
        self.assertTrue(any("corrected_fact" in error for error in errors))

    def test_explanation_and_relevance_must_be_korean(self) -> None:
        payload = evidence_payload()
        payload["results"][0]["explanation"] = "The arithmetic result is different."
        payload["results"][0]["sources"][0]["why_relevant"] = "Defines addition."
        errors = MODULE.validate_evidence(payload, {"C1"})
        self.assertTrue(any("explanation must be written in Korean" in error for error in errors))
        self.assertTrue(any("why_relevant must be written in Korean" in error for error in errors))

    def test_merge_and_apply_renders_explanatory_section_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            note = root / "note.md"
            transcript = root / "transcript.txt"
            note.write_text("# 회의\n\n## 1. 본문\n- 내용\n", encoding="utf-8")
            transcript.write_text("[10.00 - 14.00] A: 1 더하기 1은 3이다.\n", encoding="utf-8")
            result = MODULE.merge_results(
                candidate_payload(),
                evidence_payload(),
                note=note,
                transcript=transcript,
                web_search_observed=True,
            )
            MODULE.apply_to_note(note, result)
            MODULE.apply_to_note(note, result)
            rendered = note.read_text(encoding="utf-8")
            self.assertEqual(1, rendered.count(MODULE.SECTION_HEADING))
            self.assertIn("### F1. 객관 사실과 충돌", rendered)
            self.assertIn("- 바로잡기: 표준 산술에서 1 + 1은 2다.", rendered)
            self.assertIn("- 틀린 이유:", rendered)
            self.assertIn("https://example.org/arithmetic", rendered)

    def test_source_title_escapes_markdown_brackets(self) -> None:
        payload = evidence_payload()
        payload["results"][0]["sources"][0]["title"] = "기준 [별표 6]"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            note = root / "note.md"
            transcript = root / "transcript.txt"
            note.write_text("# 회의\n", encoding="utf-8")
            transcript.write_text("[10 - 11] A: 명제\n", encoding="utf-8")
            result = MODULE.merge_results(
                candidate_payload(),
                payload,
                note=note,
                transcript=transcript,
                web_search_observed=True,
            )
            rendered = MODULE.render_markdown(result)
            self.assertIn("[기준 (별표 6)](https://example.org/arithmetic)", rendered)

    def test_speaker_conflict_does_not_choose_a_winner(self) -> None:
        candidates = candidate_payload(checkability="speaker_conflict", web=False)
        candidates["candidates"][0]["counterpart_claims"] = [
            {"speaker": "B", "start_seconds": 20.0, "claim_text": "담당 조직은 B팀이다."}
        ]
        candidates["candidates"][0]["claim_text"] = "담당 조직은 A팀이다."
        result = MODULE.merge_results(
            candidates,
            {"version": 1, "web_search_performed": False, "results": []},
            note=self._temp_file("# 회의\n"),
            transcript=self._temp_file("[10 - 11] A: 담당 조직은 A팀이다.\n"),
            web_search_observed=False,
        )
        self.assertEqual("speaker_conflict", result["items"][0]["verdict"])
        self.assertIn("정할 수 없다", result["items"][0]["explanation"])
        self.assertEqual([], result["items"][0]["sources"])

    def test_stt_risk_downgrades_external_verdict_until_audio_review(self) -> None:
        candidates = candidate_payload()
        candidates["candidates"][0]["stt_risk"] = True
        result = MODULE.merge_results(
            candidates,
            evidence_payload(),
            note=self._temp_file("# 회의\n"),
            transcript=self._temp_file("[10 - 11] A: 1 더하기 1은 3이다.\n"),
            web_search_observed=True,
        )
        item = result["items"][0]
        self.assertEqual("stt_review_needed", item["verdict"])
        self.assertEqual("low", item["confidence"])
        self.assertIn("원음", item["explanation"])
        self.assertTrue(item["sources"])

    def test_confirmed_audio_review_allows_external_verdict(self) -> None:
        candidates = candidate_payload()
        candidates["candidates"][0]["stt_risk"] = True
        audio_review = {
            "version": 1,
            "results": [
                {
                    "candidate_id": "C1",
                    "status": "confirmed",
                    "explanation": "별도 구간 재전사에서도 같은 계산 명제가 확인됐다.",
                    "supporting_text": "1 더하기 1은 3이다.",
                }
            ],
        }
        result = MODULE.merge_results(
            candidates,
            evidence_payload(),
            note=self._temp_file("# 회의\n"),
            transcript=self._temp_file("[10 - 11] A: 1 더하기 1은 3이다.\n"),
            web_search_observed=True,
            audio_review=audio_review,
        )
        item = result["items"][0]
        self.assertEqual("contradicted", item["verdict"])
        self.assertEqual("confirmed", item["audio_review"]["status"])
        rendered = MODULE.render_markdown(result)
        self.assertIn("구간 재전사에서 명제 확인", rendered)
        self.assertNotIn("원음 재청취 필요", rendered)

    def test_internal_not_assessable_items_are_compact_in_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            note = root / "note.md"
            transcript = root / "transcript.txt"
            note.write_text("# 회의\n", encoding="utf-8")
            transcript.write_text("[1 - 2] A: 내부 상태다.\n", encoding="utf-8")
            candidates = candidate_payload(checkability="internal_authority", web=False)
            candidates["candidates"][0]["required_evidence"] = "승인된 내부 상태 기록"
            result = MODULE.merge_results(
                candidates,
                {"version": 1, "web_search_performed": False, "results": []},
                note=note,
                transcript=transcript,
                web_search_observed=False,
            )
            rendered = MODULE.render_markdown(result)
            self.assertIn("### 추가 확인이 필요한 명제", rendered)
            self.assertIn("확인할 정본: 승인된 내부 상태 기록", rendered)

    def _temp_file(self, text: str) -> Path:
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
        handle.write(text)
        handle.close()
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)
        return Path(handle.name)


if __name__ == "__main__":
    unittest.main()
