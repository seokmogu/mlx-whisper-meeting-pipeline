from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[1] / "sh" / "render_meeting_attendee_context.py"
)
SPEC = importlib.util.spec_from_file_location(
    "render_meeting_attendee_context", MODULE_PATH
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RenderMeetingAttendeeContextTests(unittest.TestCase):
    def test_renders_only_current_attendee_identities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attendees = root / "attendees.txt"
            identities = root / "identities.tsv"
            attendees.write_text("구석모, 이준, 김수빈\n", encoding="utf-8")
            identities.write_text(
                "name\tdepartment\tposition\tstatus\n"
                "이준\t사업운영부문\t부문장\texact\n"
                "김수빈\t경영기획팀\t\texact\n"
                "정승현\t나인하이어사업실\t실장\texact\n",
                encoding="utf-8",
            )

            values = MODULE.load_attendees(attendees)
            labels = MODULE.identity_labels(
                values, MODULE.load_identities(identities)
            )

            self.assertEqual(values, ["구석모", "이준", "김수빈"])
            self.assertEqual(
                labels,
                ["이준(사업운영부문, 부문장)", "김수빈(경영기획팀)"],
            )
            rendered = MODULE.render(values, labels, markdown=True)
            self.assertIn("사용자 확정 참석자: 구석모, 이준, 김수빈", rendered)
            self.assertIn("이준(사업운영부문, 부문장)", rendered)
            self.assertNotIn("정승현", rendered)

    def test_missing_identity_file_keeps_confirmed_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attendees = root / "attendees.txt"
            attendees.write_text("구석모, 고병삼\n", encoding="utf-8")

            values = MODULE.load_attendees(attendees)
            labels = MODULE.identity_labels(
                values, MODULE.load_identities(root / "missing.tsv")
            )

            self.assertEqual(labels, [])
            self.assertEqual(
                MODULE.render(values, labels, markdown=False),
                "사용자 확정 참석자:\n구석모, 고병삼\n",
            )


if __name__ == "__main__":
    unittest.main()
