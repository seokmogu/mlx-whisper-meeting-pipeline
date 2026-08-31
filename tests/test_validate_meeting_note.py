from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


REPO = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = REPO / "sh" / "validate_meeting_note.py"
SPEC = importlib.util.spec_from_file_location("validate_meeting_note", VALIDATOR_PATH)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


VALID_NOTE = """# AX 운영 회의

- 일시: 2026-08-18 09:55
- 원본: transcript.txt
- 작성 방식: AI 추정

## 1. 핵심 결론 및 결정사항
- **방향 합의 · 팀별 과제를 기준으로 AX 프로그램을 운영한다** (§4.1)

## 2. Action Items
*표기 순서: 담당 범위 · 기한 · 근거 섹션*

- [ ] **A1 · 승인된 설문 문항 확정**
  담당자 확인 필요 · 9월 전 · §4.1

## 3. 미결 쟁점 및 다음 결정
- **M1 · A1의 실제 담당자 확정**
  담당자 확인 필요 · 9월 전 · §4.1

## 4. 상세 논의와 근거
### 4.1 운영 방식
- 화자 B가 팀별 운영 필요성을 확인했다.

## 5. 참석자·용어 검증 부록
### 5.1 참석자/언급 인물
- 확인 필요

### 5.2 검증 완료
- 해당 없음

### 5.3 검증 필요
- 참석자
"""


class ValidateMeetingNoteTest(unittest.TestCase):
    def test_valid_reader_first_note_passes(self) -> None:
        self.assertEqual([], VALIDATOR.validate_note(VALID_NOTE))

    def test_allows_valid_objective_fact_check_as_last_section(self) -> None:
        note = VALID_NOTE + """

## 6. 객관 명제 팩트체크

- 범위: 객관 명제만 검토했다.
- 웹검색: 실제 실행 확인
- 결과: 객관 사실과 충돌 1건

### F1. 객관 사실과 충돌
- 발언: “1 더하기 1은 3이다.”
- 위치: 화자 A · 00:00:10
- 바로잡기: 표준 산술에서 1 + 1은 2다.
- 틀린 이유: 덧셈 규칙과 계산 결과가 다르다.
- 근거: [공식 산술 기준](https://example.org/arithmetic) — 덧셈 정의
- 확신도: 높음
"""
        self.assertEqual([], VALIDATOR.validate_note(note))

    def test_rejects_unexplained_contradiction(self) -> None:
        note = VALID_NOTE + """

## 6. 객관 명제 팩트체크

- 웹검색: 실제 실행 확인
- 결과: 객관 사실과 충돌 1건

### F1. 객관 사실과 충돌
- 발언: “1 더하기 1은 3이다.”
"""
        errors = VALIDATOR.validate_note(note)
        self.assertTrue(any("correction and explanation" in error for error in errors))
        self.assertTrue(any("needs evidence" in error for error in errors))

    def test_rejects_task_handoff(self) -> None:
        errors = VALIDATOR.validate_note(
            VALID_NOTE + "\n## 7. Task Handoff\n- duplicated action\n"
        )
        self.assertTrue(any("Task Handoff" in error for error in errors))

    def test_rejects_more_than_seven_summary_items(self) -> None:
        summary = "\n".join(f"- 요약 {index}" for index in range(1, 9))
        note = VALID_NOTE.replace(
            "- **방향 합의 · 팀별 과제를 기준으로 AX 프로그램을 운영한다** (§4.1)",
            summary,
        )
        errors = VALIDATOR.validate_note(note)
        self.assertTrue(any("exceeds 7 items" in error for error in errors))

    def test_rejects_summary_over_character_limit(self) -> None:
        note = VALID_NOTE.replace(
            "- **방향 합의 · 팀별 과제를 기준으로 AX 프로그램을 운영한다** (§4.1)",
            "- " + ("가" * 1401),
        )
        errors = VALIDATOR.validate_note(note)
        self.assertTrue(any("exceeds 1400 characters" in error for error in errors))

    def test_rejects_legacy_action_table(self) -> None:
        compact = (
            "*표기 순서: 담당 범위 · 기한 · 근거 섹션*\n\n"
            "- [ ] **A1 · 승인된 설문 문항 확정**\n"
            "  담당자 확인 필요 · 9월 전 · §4.1"
        )
        table = (
            "| ID | 액션 | 담당 | 기한 | 완료 기준 | 검증 상태 | 근거/의존성 |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            "| A1 | 설문 문항 확정 | 담당자 확인 필요 | 9월 전 | 문항 승인 | 확인됨 | 회의 발화 |"
        )
        note = VALID_NOTE.replace(compact, table)
        errors = VALIDATOR.validate_note(note)
        self.assertTrue(any("compact checklist" in error for error in errors))

    def test_rejects_table_in_reader_sections(self) -> None:
        compact = "- **방향 합의 · 팀별 과제를 기준으로 AX 프로그램을 운영한다** (§4.1)"
        table = (
            "| 결정 | 상태 | 근거 |\n"
            "| --- | --- | --- |\n"
            "| 팀별 운영 | 방향 합의 | 회의 발화 |"
        )
        note = VALID_NOTE.replace(compact, table)
        errors = VALIDATOR.validate_note(note)
        self.assertTrue(any("must not use a table" in error for error in errors))

    def test_rejects_duplicate_action_id(self) -> None:
        duplicate = (
            "- [ ] **A1 · 승인된 후속 계획 확정**\n"
            "  AI Product팀 · 다음 회의 전 · §4.1\n"
        )
        note = VALID_NOTE.replace("\n## 3. 미결 쟁점 및 다음 결정", "\n" + duplicate + "\n## 3. 미결 쟁점 및 다음 결정")
        errors = VALIDATOR.validate_note(note)
        self.assertTrue(any("duplicate action ID" in error for error in errors))

    def test_rejects_action_without_compact_metadata(self) -> None:
        note = VALID_NOTE.replace(
            "  담당자 확인 필요 · 9월 전 · §4.1",
            "  담당자 확인 필요 · 9월 전",
        )
        errors = VALIDATOR.validate_note(note)
        self.assertTrue(any("owner · due · §4.x" in error for error in errors))

    def test_rejects_repeated_action_field_labels(self) -> None:
        note = VALID_NOTE.replace(
            "  담당자 확인 필요 · 9월 전 · §4.1",
            "  담당: 담당자 확인 필요 · 기한: 9월 전 · §4.1",
        )
        errors = VALIDATOR.validate_note(note)
        self.assertTrue(any("field labels" in error for error in errors))

    def test_rejects_anonymized_speaker_as_action_owner(self) -> None:
        note = VALID_NOTE.replace(
            "  담당자 확인 필요 · 9월 전 · §4.1",
            "  화자 C · 9월 전 · §4.1",
        )
        errors = VALIDATOR.validate_note(note)
        self.assertTrue(any("anonymized speaker" in error for error in errors))

    def test_rejects_uncertainty_in_completed_verification(self) -> None:
        note = VALID_NOTE.replace(
            "- 해당 없음\n\n### 5.3 검증 필요",
            "- `성모` → **구석모** (후보, 확인 필요)\n\n### 5.3 검증 필요",
        )
        errors = VALIDATOR.validate_note(note)
        self.assertTrue(any("contains uncertainty" in error for error in errors))

    def test_rejects_same_source_in_completed_and_pending_verification(self) -> None:
        note = VALID_NOTE.replace(
            "- 해당 없음\n\n### 5.3 검증 필요\n- 참석자",
            "- `성모` → **구석모** (직원 명부)\n\n### 5.3 검증 필요\n- `성모` 확인 필요",
        )
        errors = VALIDATOR.validate_note(note)
        self.assertTrue(any("both completed and pending" in error for error in errors))

    def test_rejects_same_source_with_trailing_particle(self) -> None:
        note = VALID_NOTE.replace(
            "- 해당 없음\n\n### 5.3 검증 필요\n- 참석자",
            "- `10월 3,40차까지` → **10월 말** (후속 발언)\n\n### 5.3 검증 필요\n- `10월 3,40차` 원문 확인",
        )
        errors = VALIDATOR.validate_note(note)
        self.assertTrue(any("both completed and pending" in error for error in errors))

    def test_rejects_numeric_correction_in_completed_verification(self) -> None:
        note = VALID_NOTE.replace(
            "- 해당 없음\n\n### 5.3 검증 필요",
            "- `10월 3,40차` → **10월 3~4주 차** (문맥)\n\n### 5.3 검증 필요",
        )
        errors = VALIDATOR.validate_note(note)
        self.assertTrue(any("protected numeric/date" in error for error in errors))

    def test_rejects_non_contiguous_action_ids(self) -> None:
        note = VALID_NOTE.replace("**A1 · 승인된", "**A2 · 승인된")
        errors = VALIDATOR.validate_note(note)
        self.assertTrue(any("IDs must be contiguous" in error for error in errors))

    def test_rejects_inline_code_in_reader_sections(self) -> None:
        note = VALID_NOTE.replace("팀별 과제", "`팀별 과제`")
        errors = VALIDATOR.validate_note(note)
        self.assertTrue(any("must not use inline code" in error for error in errors))

    def test_rejects_legacy_h2_structure(self) -> None:
        note = VALID_NOTE.replace(
            "## 1. 핵심 결론 및 결정사항",
            "## 1. 한눈에 보기",
        )
        errors = VALIDATOR.validate_note(note)
        self.assertTrue(any("unexpected H2 section" in error for error in errors))

    def test_rejects_repeated_bilingual_product_name_in_primary_sections(self) -> None:
        note = VALID_NOTE.replace(
            "팀별 과제를 기준으로 AX 프로그램을 운영한다",
            "니카(Nika)로 팀별 과제를 운영한다",
        ).replace(
            "승인된 설문 문항 확정",
            "니카(Nika) 설문 문항 확정",
        ).replace(
            "화자 B가 팀별 운영 필요성을 확인했다.",
            "화자 B가 니카(Nika) 운영 필요성을 확인했다.",
        )
        errors = VALIDATOR.validate_note(note)
        self.assertTrue(any("B-1" in error for error in errors))

    def test_rejects_four_consecutive_formulaic_reporting_endings(self) -> None:
        repeated = "\n".join(
            [
                "- 운영 현황을 설명했다.",
                "- 일정 제약을 설명했다.",
                "- 담당 범위를 설명했다.",
                "- 후속 계획을 설명했다.",
            ]
        )
        note = VALID_NOTE.replace(
            "- 화자 B가 팀별 운영 필요성을 확인했다.",
            repeated,
        )
        errors = VALIDATOR.validate_note(note)
        self.assertTrue(any("E-2" in error for error in errors))

    def test_allows_literal_bilingual_names_in_verification_appendix(self) -> None:
        note = VALID_NOTE.replace(
            "### 5.3 검증 필요\n- 참석자",
            "### 5.3 검증 필요\n- `니카(Nika)` 표기 확인\n- `니카(Nika)` 원문 확인\n- `니카(Nika)` 제품명 확인",
        )
        errors = VALIDATOR.validate_note(note)
        self.assertFalse(any("B-1" in error for error in errors))

    def test_rejects_repeated_roster_boilerplate_in_attendee_list(self) -> None:
        note = VALID_NOTE.replace(
            "### 5.1 참석자/언급 인물\n- 확인 필요",
            "### 5.1 참석자/언급 인물\n"
            "- 구석모: 직원 디렉토리에서 active로 확인됨.\n"
            "- 정승호: 직원 디렉토리에서 active로 확인됨.",
        )
        errors = VALIDATOR.validate_note(note)
        self.assertTrue(any("roster boilerplate" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
