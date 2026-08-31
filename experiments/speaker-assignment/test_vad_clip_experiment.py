from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


EXPERIMENT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EXPERIMENT_DIR))

from vad_clip_experiment import build_clips, compare  # noqa: E402


class VadClipExperimentTest(unittest.TestCase):
    def test_build_clips_pads_merges_and_clamps(self):
        payload = {
            "segments": [
                {"start": 0.1, "end": 1.0, "speaker": "A"},
                {"start": 1.2, "end": 2.0, "speaker": "B"},
                {"start": 4.0, "end": 5.2, "speaker": "A"},
            ]
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "diarization.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            clips = build_clips(
                path,
                audio_duration=5.0,
                pad_seconds=0.25,
                merge_gap_seconds=0.5,
            )
        self.assertEqual(clips, [(0.0, 2.25), (3.75, 5.0)])

    def test_report_contains_counts_and_hashes_not_transcript_text(self):
        full = {
            "segments": [
                {"text": "안 됩니다 20", "words": [{"start": 0.0, "end": 0.4}]}
            ]
        }
        clipped = {
            "segments": [
                {"text": "안 됩니다 20", "words": [{"start": 0.0, "end": 0.4}]}
            ]
        }
        report = compare(
            full,
            clipped,
            [(0.0, 1.0)],
            full_seconds=2.0,
            clipped_seconds=1.0,
            audio_duration=2.0,
        )
        rendered = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("안 됩니다", rendered)
        self.assertEqual(report["runtime_speedup"], 2.0)
        self.assertEqual(report["full_words_outside_vad_clips"], 0)


if __name__ == "__main__":
    unittest.main()
