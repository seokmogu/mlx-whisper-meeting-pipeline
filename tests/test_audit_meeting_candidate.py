from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "sh" / "audit_meeting_candidate.py"
SPEC = importlib.util.spec_from_file_location("audit_meeting_candidate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


NOTE = """# AX 운영 회의

- 일시: 2026-08-20 10:00:00
- 원본: {transcript}
- 작성 방식: AI 추정

## 1. 핵심 결론 및 결정사항
- **방향 합의 · 팀별 과제를 운영한다.** (§4.1)

## 2. Action Items
*표기 순서: 담당 범위 · 기한 · 근거 섹션*

- [ ] **A1 · 설문 문항 확정**
  구석모 · 9월 전 · §4.1

## 3. 미결 쟁점 및 다음 결정
- **M1 · 승인자 확정**
  구석모 · 9월 전 · §4.1

## 4. 상세 논의와 근거
### 4.1 운영 방식
- 구석모가 팀별 운영안을 요청했다.

## 5. 참석자·용어 검증 부록
### 5.1 참석자/언급 인물
- 사용자 확정 참석자: 구석모(AI Product팀, 팀장)

### 5.2 검증 완료
- 해당 없음

### 5.3 검증 필요
- 해당 없음
"""


class AuditMeetingCandidateTest(unittest.TestCase):
    def make_workspace(self, root: Path) -> tuple[Path, Path, Path]:
        source_base = root / "source"
        batch = root / "batch"
        transcript = source_base / "transcripts" / "worxphere" / "20260820_100000.txt"
        transcript.parent.mkdir(parents=True)
        transcript.write_text("[00:00 - 00:01] A: 팀별 운영안을 확정해 주세요.\n", encoding="utf-8")
        roster = source_base / "glossary" / "employee_roster.tsv"
        roster.parent.mkdir(parents=True)
        roster.write_text("구석모\tAI Product팀\t팀장\n", encoding="utf-8")
        attendees = source_base / "state" / "meeting-attendees" / "worxphere" / "20260820_100000.txt"
        attendees.parent.mkdir(parents=True)
        attendees.write_text("구석모\n", encoding="utf-8")
        note = batch / "notes" / "worxphere" / "20260820_100000.md"
        note.parent.mkdir(parents=True)
        note.write_text(NOTE.format(transcript=transcript), encoding="utf-8")
        MODULE.NOTION.create_readable_candidate(
            note,
            base=batch,
            validator=MODULE.NOTION.default_validator(),
        )
        review = batch / "reviews" / "worxphere" / note.stem / "review.md"
        review.parent.mkdir(parents=True)
        review.write_text("# Review\n", encoding="utf-8")
        return source_base, batch, note

    def test_complete_candidate_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source_base, batch, note = self.make_workspace(Path(temporary))
            result = MODULE.audit_one(batch, source_base, note)
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["confirmed_attendees"], ["구석모"])

    def test_missing_confirmed_attendee_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source_base, batch, note = self.make_workspace(Path(temporary))
            note.write_text(
                note.read_text(encoding="utf-8").replace(
                    "사용자 확정 참석자: 구석모(AI Product팀, 팀장)",
                    "참석자 확인 필요",
                ),
                encoding="utf-8",
            )
            result = MODULE.audit_one(batch, source_base, note)
            self.assertEqual(result["status"], "fail")
            self.assertTrue(any("confirmed attendees missing" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
