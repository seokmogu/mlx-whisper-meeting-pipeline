from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "sh" / "run-meeting-fact-check.sh"


class RunMeetingFactCheckTest(unittest.TestCase):
    def test_runner_separates_extraction_and_observed_web_search(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            note = root / "note.md"
            transcript = root / "transcript.txt"
            output = root / "fact-check.json"
            fake_codex = root / "fake-codex"
            note.write_text("# 회의\n\n## 1. 핵심\n- 계산 논의\n", encoding="utf-8")
            transcript.write_text("[10.00 - 14.00] A: 1 더하기 1은 3이다.\n", encoding="utf-8")
            fake_codex.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json
                    from pathlib import Path
                    import sys

                    args = sys.argv[1:]
                    output = Path(args[args.index("-o") + 1])
                    if "--search" in args:
                        payload = {
                            "version": 1,
                            "web_search_performed": True,
                            "results": [{
                                "candidate_id": "C1",
                                "verdict": "contradicted",
                                "corrected_fact": "표준 산술에서 1 + 1은 2다.",
                                "explanation": "덧셈 규칙상 결과는 2다.",
                                "error_type": "unit_or_calculation",
                                "confidence": "high",
                                "valid_as_of": "일반 산술",
                                "sources": [{
                                    "title": "Arithmetic standard",
                                    "url": "https://example.org/arithmetic",
                                    "source_type": "standard",
                                    "locator": "Addition",
                                    "why_relevant": "덧셈 결과를 정의한다."
                                }]
                            }]
                        }
                        print(json.dumps({"type": "item.completed", "item": {"type": "web_search"}}))
                    else:
                        payload = {
                            "version": 1,
                            "candidates": [{
                                "id": "C1",
                                "speaker": "A",
                                "start_seconds": 10.0,
                                "end_seconds": 14.0,
                                "claim_text": "1 더하기 1은 3이다.",
                                "public_claim": "In standard arithmetic, 1 + 1 equals 3.",
                                "required_evidence": "",
                                "checkability": "public_web",
                                "importance": "high",
                                "stt_risk": False,
                                "web_search_allowed": True,
                                "counterpart_claims": []
                            }]
                        }
                        print(json.dumps({"type": "item.completed", "item": {"type": "agent_message"}}))
                    output.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                    """
                ),
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "CODEX_BIN": str(fake_codex),
                    "CODEX_MODEL": "default",
                    "MEETING_FACT_CHECK_MODEL": "default",
                    "MEETING_FACT_CHECK_REASONING_EFFORT": "default",
                    "MEETING_BASE_DIR": str(REPO),
                }
            )
            result = subprocess.run(
                [str(RUNNER), "--note", str(note), "--transcript", str(transcript), "--out-json", str(output)],
                text=True,
                errors="replace",
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(payload["web_search_observed"])
            self.assertEqual("contradicted", payload["items"][0]["verdict"])
            rendered = note.read_text(encoding="utf-8")
            self.assertIn("## 6. 객관 명제 팩트체크", rendered)
            self.assertIn("https://example.org/arithmetic", rendered)
            self.assertTrue((root / "fact-check.candidates.json").exists())
            self.assertTrue((root / "fact-check.search.events.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
