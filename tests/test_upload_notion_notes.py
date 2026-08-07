from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "sh" / "upload_notion_notes.py"
SPEC = importlib.util.spec_from_file_location("upload_notion_notes", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ParticipantExtractionTests(unittest.TestCase):
    def test_parses_explicit_voice_memo_name_list(self) -> None:
        self.assertEqual(
            MODULE.parse_voice_memo_attendees("정승호, 구석모, 고병삼 미팅\n"),
            "정승호, 구석모, 고병삼",
        )

    def test_rejects_generic_voice_memo_title(self) -> None:
        self.assertEqual(MODULE.parse_voice_memo_attendees("새로운 녹음 24"), "")
        self.assertEqual(MODULE.parse_voice_memo_attendees("서초동 24"), "")

    def test_collects_only_attendees_not_mentioned_people(self) -> None:
        content = """## 10. 참석자/언급 인물
- 참석자: 구석모
- 참석자: 이준
- 언급된 인물: 고병삼
"""
        self.assertEqual(MODULE.extract_participants(content), "구석모, 이준")

    def test_confirmed_attendee_metadata_wins_over_voice_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            note = base / "notes" / "worxphere" / "20260721_120000.md"
            note.parent.mkdir(parents=True)
            note.write_text("# 회의\n", encoding="utf-8")

            attendee = (
                base
                / "state"
                / "meeting-attendees"
                / "worxphere"
                / "20260721_120000.txt"
            )
            attendee.parent.mkdir(parents=True)
            attendee.write_text("고병삼, 구석모\n", encoding="utf-8")

            voice_title = (
                base
                / "state"
                / "voice-memo-titles"
                / "worxphere"
                / "20260721_120000.txt"
            )
            voice_title.parent.mkdir(parents=True)
            voice_title.write_text("다른사람, 구석모\n", encoding="utf-8")

            self.assertEqual(
                MODULE.resolve_participants(note, base, note.read_text(encoding="utf-8")),
                "고병삼, 구석모",
            )


if __name__ == "__main__":
    unittest.main()
