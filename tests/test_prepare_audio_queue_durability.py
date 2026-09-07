from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "sh" / "prepare-audio-queue.py"
SPEC = importlib.util.spec_from_file_location("prepare_audio_queue", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class PrepareAudioQueueDurabilityTests(unittest.TestCase):
    def make_group(self, root: Path):
        base = root / "base"
        audio = base / "audio" / "worxphere"
        audio.mkdir(parents=True)
        items = []
        for suffix, second in (("a", 0), ("b", 30)):
            path = audio / f"20260101_1200{second:02d}_{suffix}.m4a"
            path.write_bytes(suffix.encode("utf-8"))
            items.append(MODULE.AudioItem(path, "worxphere", datetime(2026, 1, 1, 12, 0, second), 20.0))
        return base, audio, items

    def fake_ffmpeg(self, command, **_kwargs):
        Path(command[-1]).write_bytes(b"merged")
        return subprocess.CompletedProcess(command, 0)

    def assert_recovered_merge(self, base: Path, audio: Path) -> None:
        MODULE._recover_merge_transactions(base, "worxphere")
        target = audio / "20260101_120000_merged.m4a"
        self.assertEqual(b"merged", target.read_bytes())
        self.assertEqual([target], sorted(audio.glob("*.m4a")))
        backups = base / "state" / "audio-segments" / "worxphere" / target.stem
        self.assertEqual(
            {b"a", b"b"},
            {(backups / "20260101_120000_a.m4a").read_bytes(), (backups / "20260101_120030_b.m4a").read_bytes()},
        )
        self.assertFalse((backups / "merge-transaction.json").exists())
        before = sorted(path.relative_to(base) for path in base.rglob("*"))
        MODULE._recover_merge_transactions(base, "worxphere")
        self.assertEqual(before, sorted(path.relative_to(base) for path in base.rglob("*")))

    def snapshot(self, root: Path) -> dict[Path, bytes]:
        return {
            path.relative_to(root): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    def test_recovery_finishes_transaction_when_failure_occurs_before_first_source_move(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base, audio, items = self.make_group(Path(temporary))
            with patch.object(MODULE.subprocess, "run", self.fake_ffmpeg), patch.object(
                MODULE.shutil, "move", side_effect=OSError("injected before first move")
            ):
                with self.assertRaises(OSError):
                    MODULE._merge_group(base, items, dry_run=False)
            self.assertEqual(2, len(list(audio.glob("*.m4a"))))
            self.assert_recovered_merge(base, audio)

    def test_recovery_finishes_transaction_when_failure_occurs_between_source_moves(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base, audio, items = self.make_group(Path(temporary))
            real_move = MODULE.shutil.move
            calls = 0

            def fail_second_move(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected between source moves")
                return real_move(source, destination)

            with patch.object(MODULE.subprocess, "run", self.fake_ffmpeg), patch.object(
                MODULE.shutil, "move", side_effect=fail_second_move
            ):
                with self.assertRaises(OSError):
                    MODULE._merge_group(base, items, dry_run=False)
            self.assertEqual([audio / "20260101_120030_b.m4a"], sorted(audio.glob("*.m4a")))
            self.assert_recovered_merge(base, audio)

    def test_trim_replacement_failure_keeps_active_source_and_original_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "base"
            path = base / "audio" / "worxphere" / "20260101_120000.m4a"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"original")
            item = MODULE.AudioItem(path, "worxphere", datetime(2026, 1, 1, 12, 0), 10.0)
            window = MODULE.SpeechWindow(1.0, 9.0)

            with patch.object(MODULE, "_outer_speech_window", return_value=window), patch.object(
                MODULE.subprocess, "run", self.fake_ffmpeg
            ), patch.object(MODULE.os, "replace", side_effect=OSError("injected publish failure")):
                with self.assertRaises(OSError):
                    MODULE._trim_outer_silence(base, item, "-35dB", 0.5, 0.4, 1.0, dry_run=False)

            self.assertEqual(b"original", path.read_bytes())
            backup = base / "state" / "audio-originals" / "worxphere" / path.name
            self.assertEqual(b"original", backup.read_bytes())
            self.assertEqual([path], sorted(path.parent.glob("*.m4a")))
            self.assertEqual([], list((backup.parent / ".trim-staging").glob("*.m4a")))

            with patch.object(MODULE, "_outer_speech_window", return_value=window), patch.object(
                MODULE.subprocess, "run", self.fake_ffmpeg
            ), patch.object(MODULE, "_duration_seconds", return_value=8.0):
                _trimmed, did_trim = MODULE._trim_outer_silence(
                    base, item, "-35dB", 0.5, 0.4, 1.0, dry_run=False
                )
            self.assertTrue(did_trim)
            self.assertEqual([path], sorted(path.parent.glob("*.m4a")))
            self.assertEqual(b"merged", path.read_bytes())
            self.assertTrue(any(candidate.read_bytes() == b"original" for candidate in backup.parent.glob("*.m4a")))

    def test_dry_run_leaves_building_and_staged_merge_transactions_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base, audio, items = self.make_group(Path(temporary))
            state_root = base / "state" / "audio-segments" / "worxphere"
            building_dir = state_root / "20260101_120000_building"
            staged_dir = state_root / "20260101_120030_staged"
            building_dir.mkdir(parents=True)
            staged_dir.mkdir(parents=True)
            (building_dir / ".merged.stage.m4a").write_bytes(b"partial")
            (staged_dir / ".merged.stage.m4a").write_bytes(b"complete")
            MODULE._write_merge_manifest(
                building_dir / "merge-transaction.json",
                {
                    "version": 1,
                    "phase": "building",
                    "target_name": "20260101_120000_building.m4a",
                    "stage_name": ".merged.stage.m4a",
                    "sources": [{"source_name": items[0].path.name, "backup_name": items[0].path.name}],
                },
            )
            MODULE._write_merge_manifest(
                staged_dir / "merge-transaction.json",
                {
                    "version": 1,
                    "phase": "staged",
                    "target_name": "20260101_120030_staged.m4a",
                    "stage_name": ".merged.stage.m4a",
                    "sources": [{"source_name": items[1].path.name, "backup_name": items[1].path.name}],
                },
            )
            before = self.snapshot(base)
            output = io.StringIO()
            with patch.object(sys, "argv", [str(SCRIPT), "--dry-run"]), patch.dict(
                MODULE.os.environ,
                {"MEETING_BASE_DIR": str(base), "MEETING_PROJECTS": "worxphere"},
                clear=False,
            ), redirect_stdout(output):
                self.assertEqual(0, MODULE.main())
            self.assertEqual(before, self.snapshot(base))
            self.assertIn("would recover before queue preparation", output.getvalue())


if __name__ == "__main__":
    unittest.main()
