from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


EXPERIMENT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EXPERIMENT_DIR))

from asr_model_experiment import compare_models  # noqa: E402


class AsrModelExperimentTest(unittest.TestCase):
    def test_report_is_text_free(self):
        baseline = {
            "segments": [
                {"text": "안 됩니다 QA 20", "words": [{"start": 0.0, "end": 0.4}]}
            ]
        }
        candidate = {
            "segments": [
                {"text": "안 됩니다 QA 20", "words": [{"start": 0.0, "end": 0.4}]}
            ]
        }
        report = compare_models(
            baseline,
            candidate,
            baseline_seconds=2.0,
            candidate_seconds=1.0,
        )
        rendered = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("안 됩니다", rendered)
        self.assertEqual(report["runtime_speedup"], 2.0)
        self.assertEqual(report["normalized_char_similarity"], 1.0)


if __name__ == "__main__":
    unittest.main()
