from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "sh" / "sync-voice-memos.sh"


class SyncVoiceMemosDurabilityTests(unittest.TestCase):
    def run_sync(self, root: Path) -> subprocess.CompletedProcess[str]:
        environment = os.environ | {
            "HOME": str(root / "home"),
            "MEETING_BASE_DIR": str(root / "base"),
            "MEETING_PROJECTS": "worxphere",
            "VOICE_MEMO_FORCE_PROJECT": "worxphere",
            "VOICE_MEMO_USE_SEEN_STATE": "1",
            "VOICE_MEMO_MIN_AGE_SECONDS": "0",
        }
        return subprocess.run(
            ["bash", str(SCRIPT)],
            check=True,
            text=True,
            capture_output=True,
            env=environment,
        )

    def recording_dir(self, root: Path) -> Path:
        directory = (
            root
            / "home"
            / "Library"
            / "Group Containers"
            / "group.com.apple.VoiceMemos.shared"
            / "Recordings"
        )
        directory.mkdir(parents=True)
        (directory / "CloudRecordings.db").touch()
        return directory

    def make_old(self, path: Path, content: bytes) -> None:
        path.write_bytes(content)
        os.utime(path, (1, 1))

    def set_label(self, directory: Path, name: str, label: str) -> None:
        database = directory / "CloudRecordings.db"
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS ZCLOUDRECORDING (ZPATH TEXT, ZCUSTOMLABELFORSORTING TEXT, ZCUSTOMLABEL TEXT)"
            )
            connection.execute(
                "INSERT INTO ZCLOUDRECORDING VALUES (?, ?, ?)",
                (name, label, label),
            )
            connection.commit()
        finally:
            connection.close()

    def test_normalization_collision_copies_both_sources_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.recording_dir(root)
            self.make_old(source / "same name.m4a", b"first")
            self.make_old(source / "same_name.m4a", b"second")
            self.set_label(source, "same name.m4a", "first title")
            self.set_label(source, "same_name.m4a", "second title")

            self.run_sync(root)
            destination = root / "base" / "audio" / "worxphere"
            copied = sorted(destination.glob("*.m4a"))
            self.assertEqual(2, len(copied))
            self.assertEqual({b"first", b"second"}, {path.read_bytes() for path in copied})
            self.assertIn(destination / "same_name.m4a", copied)
            self.assertTrue(
                any(re.fullmatch(r"same_name-source-[0-9a-f]{12}\.m4a", path.name) for path in copied)
            )
            seen = (root / "base" / "state" / "voice-memos-seen.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual({"same name.m4a", "same_name.m4a"}, set(seen))
            self.assertEqual(2, len(list((root / "base" / "state" / "voice-memo-sources").glob("*.target"))))
            titles = root / "base" / "state" / "voice-memo-titles" / "worxphere"
            self.assertEqual({"first title\n", "second title\n"}, {path.read_text(encoding="utf-8") for path in titles.glob("*.txt")})

            self.run_sync(root)
            self.assertEqual(copied, sorted(destination.glob("*.m4a")))

    def test_existing_normalized_destination_is_not_claimed_by_new_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.recording_dir(root)
            self.make_old(source / "same name.m4a", b"new-source")
            destination = root / "base" / "audio" / "worxphere"
            destination.mkdir(parents=True)
            (destination / "same_name.m4a").write_bytes(b"legacy-source")

            self.run_sync(root)
            copied = sorted(destination.glob("*.m4a"))
            self.assertEqual(2, len(copied))
            self.assertEqual(b"legacy-source", (destination / "same_name.m4a").read_bytes())
            collision_copy = next(path for path in copied if path.name != "same_name.m4a")
            self.assertRegex(collision_copy.name, r"^same_name-source-[0-9a-f]{12}\.m4a$")
            self.assertEqual(b"new-source", collision_copy.read_bytes())


if __name__ == "__main__":
    unittest.main()
