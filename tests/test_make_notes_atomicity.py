from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

from test_validate_meeting_note import VALID_NOTE


REPO = Path(__file__).resolve().parents[1]
MAKE_NOTES = REPO / "sh" / "make-notes.sh"


class MakeNotesAtomicityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        (self.base / "sh").symlink_to(REPO / "sh", target_is_directory=True)
        (self.base / "skills").symlink_to(REPO / "skills", target_is_directory=True)
        self.transcript_dir = self.base / "transcripts" / "worxphere"
        self.transcript_dir.mkdir(parents=True)
        self.fake_codex = self.base / "fake-codex"
        self.fake_fact_check = self.base / "fake-fact-check"
        self.fake_codex.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import os
                from pathlib import Path
                import sys

                output = Path(sys.argv[sys.argv.index("-o") + 1])
                counter = Path(os.environ["FAKE_CODEX_COUNTER"])
                count = int(counter.read_text() or "0") if counter.exists() else 0
                prompt_log = os.environ.get("FAKE_PROMPT_LOG")
                if prompt_log:
                    Path(prompt_log).write_text(sys.stdin.read(), encoding="utf-8")
                if os.environ.get("FAKE_CODEX_MODE") == "fail":
                    raise SystemExit(7)
                output.write_text(Path(os.environ["FAKE_NOTE_FILE"]).read_text(encoding="utf-8"), encoding="utf-8")
                counter.write_text(str(count + 1), encoding="utf-8")
                """
            ),
            encoding="utf-8",
        )
        self.fake_fact_check.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import hashlib
                import json
                import os
                from pathlib import Path
                import sys

                args = sys.argv[1:]
                note = Path(args[args.index("--note") + 1])
                output = Path(args[args.index("--out-json") + 1])
                log = os.environ.get("FAKE_FACT_NOTE_LOG")
                if log:
                    Path(log).write_text(str(note), encoding="utf-8")
                if os.environ.get("FAKE_FACT_MODE") == "fail":
                    raise SystemExit(9)
                output.write_text(json.dumps({
                    "status": "ok",
                    "note_path": str(note.resolve()),
                    "note_sha256": hashlib.sha256(note.read_bytes()).hexdigest(),
                }) + chr(10), encoding="utf-8")
                """
            ),
            encoding="utf-8",
        )
        self.fake_codex.chmod(0o755)
        self.fake_fact_check.chmod(0o755)
        self.note_file = self.base / "generated.md"
        self.note_file.write_text(VALID_NOTE, encoding="utf-8")
        self.counter = self.base / "codex-count"
        self.fact_note_log = self.base / "fact-note-path"
        self.prompt_log = self.base / "prompt"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _env(self, *, fact_mode: str = "ok", codex_mode: str = "ok") -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "MEETING_BASE_DIR": str(self.base),
                "MEETING_PROJECTS": "worxphere",
                "CODEX_BIN": str(self.fake_codex),
                "CODEX_SEARCH": "0",
                "CODEX_MODEL": "default",
                "CODEX_REASONING_EFFORT": "default",
                "CODEX_IGNORE_RULES": "1",
                "MEETING_FACT_CHECK_RUNNER": str(self.fake_fact_check),
                "MEETING_PHONETIC_CANDIDATES": "0",
                "WDC_MEETING_CONTEXT": "0",
                "FAKE_CODEX_COUNTER": str(self.counter),
                "FAKE_NOTE_FILE": str(self.note_file),
                "FAKE_FACT_MODE": fact_mode,
                "FAKE_FACT_NOTE_LOG": str(self.fact_note_log),
                "FAKE_CODEX_MODE": codex_mode,
                "FAKE_PROMPT_LOG": str(self.prompt_log),
            }
        )
        return env

    def _run(
        self,
        name: str,
        *,
        force: bool = False,
        dry_run: bool = False,
        fact_mode: str = "ok",
        codex_mode: str = "ok",
    ) -> subprocess.CompletedProcess[str]:
        args = [str(MAKE_NOTES), "--only", f"worxphere/{name}"]
        if force:
            args.append("--force")
        if dry_run:
            args.append("--dry-run")
        return subprocess.run(
            args,
            text=True,
            capture_output=True,
            env=self._env(fact_mode=fact_mode, codex_mode=codex_mode),
            check=False,
        )

    def _assert_no_staging(self, name: str) -> None:
        self.assertEqual([], list((self.base / "notes" / "worxphere").glob(f".{name}.candidate.*")))
        self.assertEqual(
            [],
            list((self.base / "state" / "fact-checks" / "worxphere").glob(f".{name}.candidate.*")),
        )

    def test_failed_fact_check_leaves_no_note_so_a_plain_retry_runs(self) -> None:
        transcript = self.transcript_dir / "retry.txt"
        raw = "원본 전사 내용\n"
        transcript.write_text(raw, encoding="utf-8")

        failed = self._run("retry", fact_mode="fail")

        output = self.base / "notes" / "worxphere" / "retry.md"
        self.assertNotEqual(0, failed.returncode)
        self.assertFalse(output.exists())
        self.assertEqual(raw, transcript.read_text(encoding="utf-8"))
        self.assertFalse((self.base / "state" / "fact-checks" / "worxphere" / "retry.json").exists())
        self._assert_no_staging("retry")

        retried = self._run("retry")

        self.assertEqual(0, retried.returncode, retried.stderr)
        self.assertEqual(VALID_NOTE, output.read_text(encoding="utf-8"))
        self.assertEqual("2", self.counter.read_text(encoding="utf-8"))
        fact_result = self.base / "state" / "fact-checks" / "worxphere" / "retry.json"
        fact_payload = json.loads(fact_result.read_text(encoding="utf-8"))
        self.assertEqual(str(output.resolve()), fact_payload["note_path"])
        self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), fact_payload["note_sha256"])
        staged_note = Path(self.fact_note_log.read_text(encoding="utf-8"))
        self.assertNotEqual(output, staged_note)
        self.assertTrue(staged_note.name.startswith(".retry.candidate."))

    def test_failed_forced_regeneration_keeps_existing_note_and_fact_state(self) -> None:
        transcript = self.transcript_dir / "existing.txt"
        transcript.write_text("원본 전사 내용\n", encoding="utf-8")
        output = self.base / "notes" / "worxphere" / "existing.md"
        output.parent.mkdir(parents=True)
        original = VALID_NOTE.replace("# AX 운영 회의", "# 기존 AX 운영 회의", 1)
        output.write_text(original, encoding="utf-8")
        fact_state = self.base / "state" / "fact-checks" / "worxphere" / "existing.json"
        fact_state.parent.mkdir(parents=True)
        fact_state.write_text('{"status":"previous"}\n', encoding="utf-8")

        failed = self._run("existing", force=True, fact_mode="fail")

        self.assertNotEqual(0, failed.returncode)
        self.assertEqual(original, output.read_text(encoding="utf-8"))
        self.assertEqual('{"status":"previous"}\n', fact_state.read_text(encoding="utf-8"))
        self.assertFalse((self.base / "state" / "note-backups").exists())
        self._assert_no_staging("existing")

    def test_llm_failure_leaves_no_canonical_artifacts(self) -> None:
        self.transcript_dir.joinpath("llm-failure.txt").write_text("원본 전사 내용\n", encoding="utf-8")

        failed = self._run("llm-failure", codex_mode="fail")

        self.assertNotEqual(0, failed.returncode)
        self.assertFalse((self.base / "notes" / "worxphere" / "llm-failure.md").exists())
        self.assertFalse((self.base / "state" / "fact-checks" / "worxphere" / "llm-failure.json").exists())
        self.assertFalse(self.fact_note_log.exists())
        self._assert_no_staging("llm-failure")

    def test_validation_failure_leaves_no_canonical_artifacts(self) -> None:
        self.transcript_dir.joinpath("validation-failure.txt").write_text("원본 전사 내용\n", encoding="utf-8")
        self.note_file.write_text(VALID_NOTE + "\n## 7. Task Handoff\n- duplicate\n", encoding="utf-8")

        failed = self._run("validation-failure")

        self.assertNotEqual(0, failed.returncode)
        self.assertEqual("2", self.counter.read_text(encoding="utf-8"))
        self.assertFalse((self.base / "notes" / "worxphere" / "validation-failure.md").exists())
        self.assertFalse((self.base / "state" / "fact-checks" / "worxphere" / "validation-failure.json").exists())
        self.assertFalse(self.fact_note_log.exists())
        self._assert_no_staging("validation-failure")

    def test_candidate_markdown_is_excluded_from_previous_note_context(self) -> None:
        self.transcript_dir.joinpath("current.txt").write_text("원본 전사 내용\n", encoding="utf-8")
        output_dir = self.base / "notes" / "worxphere"
        output_dir.mkdir(parents=True)
        output_dir.joinpath(".earlier.candidate.leftover.md").write_text(
            "STAGED CANDIDATE MUST NOT BECOME CONTEXT\n", encoding="utf-8"
        )

        result = self._run("current")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("STAGED CANDIDATE MUST NOT BECOME CONTEXT", self.prompt_log.read_text(encoding="utf-8"))

    def test_dry_run_does_not_create_an_output_directory(self) -> None:
        self.transcript_dir.joinpath("dry-run.txt").write_text("원본 전사 내용\n", encoding="utf-8")

        result = self._run("dry-run", dry_run=True)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse((self.base / "notes").exists())


if __name__ == "__main__":
    unittest.main()
