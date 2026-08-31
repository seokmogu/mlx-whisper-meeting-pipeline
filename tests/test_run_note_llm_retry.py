from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "sh" / "run-note-llm.sh"

VALID_NOTE = """# AX 운영 회의

- 일시: 확인 필요
- 원본: transcript.txt
- 작성 방식: AI 추정

## 1. 핵심 결론 및 결정사항
- **방향 합의 · 팀별 과제를 기준으로 운영한다** (§4.1)

## 2. Action Items
*표기 순서: 담당 범위 · 기한 · 근거 섹션*

- [ ] **A1 · 승인된 설문 문항 확정**
  담당자 확인 필요 · 기한 확인 필요 · §4.1

## 3. 미결 쟁점 및 다음 결정
- **M1 · A1 담당자 확정**
  담당자 확인 필요 · 기한 확인 필요 · §4.1

## 4. 상세 논의와 근거
### 4.1 운영
- 화자 B가 팀별 운영을 확인했다.

## 5. 참석자·용어 검증 부록
### 5.1 참석자/언급 인물
- 확인 필요

### 5.2 검증 완료
- 해당 없음

### 5.3 검증 필요
- 참석자
"""


class RunNoteLlmRetryTest(unittest.TestCase):
    def test_retries_once_with_validator_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            invalid = base / "invalid.md"
            valid = base / "valid.md"
            counter = base / "counter.txt"
            output = base / "output.md"
            fake_codex = base / "fake-codex"

            invalid.write_text(
                VALID_NOTE + "\n## 7. Task Handoff\n- duplicate\n",
                encoding="utf-8",
            )
            valid.write_text(VALID_NOTE, encoding="utf-8")
            fake_codex.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import os
                    from pathlib import Path
                    import sys

                    args = sys.argv[1:]
                    output = Path(args[args.index("-o") + 1])
                    counter = Path(os.environ["FAKE_CODEX_COUNTER"])
                    count = int(counter.read_text() or "0") if counter.exists() else 0
                    source_key = "FAKE_CODEX_INVALID" if count == 0 else "FAKE_CODEX_VALID"
                    output.write_text(Path(os.environ[source_key]).read_text(encoding="utf-8"), encoding="utf-8")
                    counter.write_text(str(count + 1), encoding="utf-8")
                    """
                ),
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "CODEX_BIN": str(fake_codex),
                    "CODEX_SEARCH": "0",
                    "CODEX_MODEL": "default",
                    "CODEX_REASONING_EFFORT": "default",
                    "CODEX_IGNORE_RULES": "1",
                    "MEETING_BASE_DIR": str(REPO),
                    "FAKE_CODEX_COUNTER": str(counter),
                    "FAKE_CODEX_INVALID": str(invalid),
                    "FAKE_CODEX_VALID": str(valid),
                }
            )
            result = subprocess.run(
                [str(RUNNER), "--out", str(output)],
                input="회의 전사 원문",
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("2", counter.read_text(encoding="utf-8"))
            self.assertEqual(VALID_NOTE, output.read_text(encoding="utf-8"))
            self.assertIn("retrying once", result.stderr)


if __name__ == "__main__":
    unittest.main()
