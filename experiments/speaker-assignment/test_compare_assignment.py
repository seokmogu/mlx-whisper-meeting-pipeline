from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


EXPERIMENT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EXPERIMENT_DIR))

from compare_assignment import (  # noqa: E402
    Turn,
    WordInterval,
    assign_word_reference,
    assign_words_by_overlap,
    evaluate_fixtures,
)


class SpeakerAssignmentExperimentTest(unittest.TestCase):
    def test_fixture_contract(self):
        result = evaluate_fixtures(EXPERIMENT_DIR / "fixtures" / "cases.json", uncertainty_margin=0.15)
        self.assertEqual(result["passed"], result["total"])
        self.assertGreaterEqual(result["total"], 5)

    def test_sweep_matches_all_turn_reference(self):
        turns = [
            Turn(0.0, 1.0, "A"),
            Turn(0.7, 1.4, "B"),
            Turn(1.4, 2.0, "A"),
        ]
        words = [
            WordInterval(0.1, 0.4),
            WordInterval(0.8, 1.1),
            WordInterval(1.2, 1.5),
            WordInterval(2.2, 2.5),
        ]
        actual = assign_words_by_overlap(words, turns)
        expected = [assign_word_reference(word, turns) for word in words]
        self.assertEqual(actual, expected)

    def test_real_report_loader_does_not_need_word_text(self):
        payload = json.loads((EXPERIMENT_DIR / "fixtures" / "cases.json").read_text(encoding="utf-8"))
        self.assertTrue(all("text" not in case["word"] for case in payload["cases"]))

    def test_rejects_unsorted_words(self):
        with self.assertRaisesRegex(ValueError, "sorted"):
            assign_words_by_overlap(
                [WordInterval(1.0, 1.2), WordInterval(0.0, 0.2)],
                [Turn(0.0, 2.0, "A")],
            )


if __name__ == "__main__":
    unittest.main()
