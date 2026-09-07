from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "sh" / "format_transcript.py"
SPEC = importlib.util.spec_from_file_location("format_transcript", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FormatTranscriptAtomicityTests(unittest.TestCase):
    def test_failed_publish_preserves_existing_transcript_and_cleans_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "transcript.txt"
            destination.write_text("complete prior transcript\n", encoding="utf-8")

            with patch.object(MODULE.os, "replace", side_effect=OSError("injected publish failure")):
                with self.assertRaises(OSError):
                    MODULE.atomic_write_text(destination, "partial replacement\n")

            self.assertEqual("complete prior transcript\n", destination.read_text(encoding="utf-8"))
            self.assertEqual([], list(destination.parent.glob(".transcript.txt.*")))

    def test_successful_publish_replaces_only_after_completed_temp_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "transcript.txt"
            destination.write_text("old\n", encoding="utf-8")
            MODULE.atomic_write_text(destination, "new complete transcript\n")
            self.assertEqual("new complete transcript\n", destination.read_text(encoding="utf-8"))
            self.assertEqual([], list(destination.parent.glob(".transcript.txt.*")))

    def test_failed_fsync_cleans_temp_file_without_touching_existing_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "transcript.txt"
            destination.write_text("complete prior transcript\n", encoding="utf-8")
            with patch.object(MODULE.os, "fsync", side_effect=OSError("injected fsync failure")):
                with self.assertRaises(OSError):
                    MODULE.atomic_write_text(destination, "replacement\n")
            self.assertEqual("complete prior transcript\n", destination.read_text(encoding="utf-8"))
            self.assertEqual([], list(destination.parent.glob(".transcript.txt.*")))


if __name__ == "__main__":
    unittest.main()
