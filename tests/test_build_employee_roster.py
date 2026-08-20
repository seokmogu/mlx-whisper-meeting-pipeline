from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "sh" / "build_employee_roster.py"
SPEC = importlib.util.spec_from_file_location("build_employee_roster", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

PHONETIC_PATH = Path(__file__).parents[1] / "sh" / "phonetic_name_candidates.py"
PHONETIC_SPEC = importlib.util.spec_from_file_location("phonetic_name_candidates_for_roster_test", PHONETIC_PATH)
assert PHONETIC_SPEC and PHONETIC_SPEC.loader
PHONETIC = importlib.util.module_from_spec(PHONETIC_SPEC)
PHONETIC_SPEC.loader.exec_module(PHONETIC)
BUILD_SCRIPT = Path(__file__).parents[1] / "sh" / "build_employee_roster.sh"


def employee(name: str, email: str, department: str = "AI Product팀"):
    return MODULE.Employee(
        identity_key=MODULE._identity_key(email=email),
        name=name,
        department=department,
        position="",
        source="familybab",
    )


def familybab_fixture() -> str:
    return """---
kind: familybab-index
---
# 한솥밥식구
## AI Product팀 (1명)
| 이름 | 직책 | 내선 | 휴대폰 | 이메일 | 입사일 | 생일 |
| --- | --- | --- | --- | --- | --- | --- |
| 김민수 | PO | | | person@jobkorea.co.kr | | |
"""


class EmployeeRosterHistoryTest(unittest.TestCase):
    def test_company_email_aliases_share_an_internal_identity_key(self) -> None:
        self.assertEqual(
            MODULE._identity_key(email="samko@jobkorea.co.kr"),
            MODULE._identity_key(email="SAMKO@worxphere.ai"),
        )

    def test_control_characters_are_removed_from_identity_text(self) -> None:
        self.assertEqual(
            ("허홍범", "AI Product팀"),
            MODULE._split_wdc_name_team("\x08허홍범_AI Product팀"),
        )

    def test_same_name_people_remain_distinct_by_email(self) -> None:
        first = employee("김민수", "minsu.one@jobkorea.co.kr", "Product팀")
        second = employee("김민수", "minsu.two@jobkorea.co.kr", "Sales팀")
        history, _ = MODULE.update_history(
            {"version": 1, "entries": {}},
            [first, second],
            [],
            snapshot_date="2026-08-20",
            source_is_fresh=True,
        )
        self.assertEqual(2, len(history["entries"]))

    def test_unique_name_fallback_survives_email_alias_change(self) -> None:
        old, _ = MODULE.update_history(
            {"version": 1, "entries": {}},
            [employee("구석모", "old.alias@jobkorea.co.kr")],
            [],
            snapshot_date="2026-07-01",
            source_is_fresh=False,
        )
        current, _ = MODULE.update_history(
            old,
            [employee("구석모", "new.alias@jobkorea.co.kr")],
            [],
            snapshot_date="2026-08-20",
            source_is_fresh=True,
        )
        self.assertEqual(1, len(current["entries"]))
        entry = next(iter(current["entries"].values()))
        self.assertEqual("active", entry["employment_status"])

    def test_departed_employee_is_retained_and_marked_former(self) -> None:
        first, _ = MODULE.update_history(
            {"version": 1, "entries": {}},
            [employee("구석모", "samko@example.com"), employee("고병삼", "ko@example.com")],
            [],
            snapshot_date="2026-08-19",
            source_is_fresh=True,
        )
        second, transitions = MODULE.update_history(
            first,
            [employee("구석모", "samko@example.com")],
            [],
            snapshot_date="2026-08-20",
            source_is_fresh=True,
        )

        by_name = {entry["name"]: entry for entry in second["entries"].values()}
        self.assertEqual("active", by_name["구석모"]["employment_status"])
        self.assertEqual("former", by_name["고병삼"]["employment_status"])
        self.assertEqual("2026-08-20", by_name["고병삼"]["former_since"])
        self.assertEqual(1, transitions["former"])

    def test_stale_snapshot_cannot_mark_departure_or_reactivate(self) -> None:
        first, _ = MODULE.update_history(
            {"version": 1, "entries": {}},
            [employee("구석모", "samko@example.com"), employee("고병삼", "ko@example.com")],
            [],
            snapshot_date="2026-08-19",
            source_is_fresh=True,
        )
        second, transitions = MODULE.update_history(
            first,
            [employee("구석모", "samko@example.com")],
            [],
            snapshot_date="2026-07-01",
            source_is_fresh=False,
        )

        by_name = {entry["name"]: entry for entry in second["entries"].values()}
        self.assertEqual("active", by_name["고병삼"]["employment_status"])
        self.assertEqual("2026-08-19", by_name["구석모"]["last_confirmed"])
        self.assertEqual(0, transitions["former"])

    def test_person_from_old_snapshot_becomes_former_when_fresh_snapshot_omits_them(self) -> None:
        old, _ = MODULE.update_history(
            {"version": 1, "entries": {}},
            [employee("고병삼", "ko@example.com")],
            [],
            snapshot_date="2026-07-01",
            source_is_fresh=False,
        )
        current, _ = MODULE.update_history(
            old,
            [],
            [],
            snapshot_date="2026-08-20",
            source_is_fresh=True,
        )
        entry = next(iter(current["entries"].values()))
        self.assertEqual("former", entry["employment_status"])
        self.assertEqual("2026-07-01", entry["last_confirmed"])

    def test_wdc_only_person_is_unverified_and_cannot_reactivate_former(self) -> None:
        known = employee("고병삼", "ko@example.com")
        first, _ = MODULE.update_history(
            {"version": 1, "entries": {}},
            [known],
            [],
            snapshot_date="2026-08-19",
            source_is_fresh=True,
        )
        former, _ = MODULE.update_history(
            first,
            [],
            [],
            snapshot_date="2026-08-20",
            source_is_fresh=True,
        )
        wdc_match = MODULE.Employee(
            **{
                **known.__dict__,
                "identity_key": MODULE._identity_key(email="ko@worxphere.ai"),
                "source": "wdc",
            }
        )
        result, _ = MODULE.update_history(
            former,
            [],
            [wdc_match, employee("새사용자", "new@example.com")],
            snapshot_date="2026-08-20",
            source_is_fresh=True,
        )

        by_name = {entry["name"]: entry for entry in result["entries"].values()}
        self.assertEqual("former", by_name["고병삼"]["employment_status"])
        self.assertEqual("unverified", by_name["새사용자"]["employment_status"])

    def test_roster_output_has_status_but_no_identity_or_email(self) -> None:
        history, _ = MODULE.update_history(
            {"version": 1, "entries": {}},
            [employee("구석모", "samko@example.com")],
            [],
            snapshot_date="2026-08-20",
            source_is_fresh=True,
        )
        rendered = MODULE.render_roster(history)
        self.assertIn("employment_status", rendered)
        self.assertIn("구석모\tAI Product팀\t\tactive\t2026-08-20", rendered)
        self.assertNotIn("samko@example.com", rendered)
        self.assertNotIn(next(iter(history["entries"])), rendered)

    def test_write_if_changed_preserves_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roster.tsv"
            self.assertTrue(MODULE.write_if_changed(path, "same\n"))
            original_mtime = path.stat().st_mtime_ns
            self.assertFalse(MODULE.write_if_changed(path, "same\n"))
            self.assertEqual(original_mtime, path.stat().st_mtime_ns)

    def test_phonetic_candidates_keep_employment_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            roster = Path(directory) / "roster.tsv"
            roster.write_text(
                "name\tdepartment\tposition\temployment_status\tlast_confirmed\tsource\n"
                "구석모\tAI Product팀\t팀장\tformer\t2026-08-19\tfamilybab\n",
                encoding="utf-8",
            )
            rows = PHONETIC.load_roster(roster)
            self.assertEqual("former", rows[0][3])
            self.assertEqual("구석모", PHONETIC.best_matches("성모", rows, threshold=0.4, top=1)[0][1])

    def test_shell_uses_macbook_collector_after_remote_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "sh").mkdir()
            (base / "sh" / "build_employee_roster.py").symlink_to(MODULE_PATH)
            source = base / "familybab-index.md"
            source.write_text(familybab_fixture(), encoding="utf-8")
            old_epoch = time.time() - 3 * 86400
            os.utime(source, (old_epoch, old_epoch))
            collector = base / "collector.sh"
            collector.write_text('#!/bin/bash\ntouch "$MOCK_OUTPUT"\n', encoding="utf-8")
            collector.chmod(0o755)
            env = {
                **os.environ,
                "MEETING_BASE_DIR": str(base),
                "EMPLOYEE_DIRECTORY_INDEX": str(source),
                "EMPLOYEE_DIRECTORY_REMOTE_HOST": "127.0.0.1",
                "EMPLOYEE_DIRECTORY_REMOTE_INDEX": "/missing/familybab-index.md",
                "EMPLOYEE_DIRECTORY_REMOTE_CONNECT_TIMEOUT_SECONDS": "1",
                "EMPLOYEE_DIRECTORY_REMOTE_CHECK_INTERVAL_SECONDS": "0",
                "EMPLOYEE_DIRECTORY_MAX_AGE_HOURS": "1",
                "EMPLOYEE_DIRECTORY_LOCAL_PROJECT": str(base),
                "EMPLOYEE_DIRECTORY_LOCAL_COLLECTOR": str(collector),
                "WDC_NOTION_USERS_PATH": str(base / "missing-users.json"),
                "MOCK_OUTPUT": str(source),
            }
            result = subprocess.run([str(BUILD_SCRIPT)], env=env, text=True, capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr)
            status = json.loads((base / "state" / "employee-roster" / "status.json").read_text())
            self.assertEqual("macbook_fallback", status["source_origin"])
            self.assertEqual("success", status["local_fallback_result"])
            self.assertTrue(status["source_is_fresh"])

    def test_shell_keeps_stale_history_when_both_collectors_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "sh").mkdir()
            (base / "sh" / "build_employee_roster.py").symlink_to(MODULE_PATH)
            source = base / "familybab-index.md"
            source.write_text(familybab_fixture(), encoding="utf-8")
            old_epoch = time.time() - 3 * 86400
            os.utime(source, (old_epoch, old_epoch))
            collector = base / "collector.sh"
            collector.write_text("#!/bin/bash\nexit 9\n", encoding="utf-8")
            collector.chmod(0o755)
            env = {
                **os.environ,
                "MEETING_BASE_DIR": str(base),
                "EMPLOYEE_DIRECTORY_INDEX": str(source),
                "EMPLOYEE_DIRECTORY_REMOTE_HOST": "127.0.0.1",
                "EMPLOYEE_DIRECTORY_REMOTE_INDEX": "/missing/familybab-index.md",
                "EMPLOYEE_DIRECTORY_REMOTE_CONNECT_TIMEOUT_SECONDS": "1",
                "EMPLOYEE_DIRECTORY_REMOTE_CHECK_INTERVAL_SECONDS": "0",
                "EMPLOYEE_DIRECTORY_MAX_AGE_HOURS": "1",
                "EMPLOYEE_DIRECTORY_LOCAL_PROJECT": str(base),
                "EMPLOYEE_DIRECTORY_LOCAL_COLLECTOR": str(collector),
                "WDC_NOTION_USERS_PATH": str(base / "missing-users.json"),
            }
            result = subprocess.run([str(BUILD_SCRIPT)], env=env, text=True, capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr)
            status = json.loads((base / "state" / "employee-roster" / "status.json").read_text())
            self.assertEqual("failed", status["local_fallback_result"])
            self.assertFalse(status["source_is_fresh"])


if __name__ == "__main__":
    unittest.main()
