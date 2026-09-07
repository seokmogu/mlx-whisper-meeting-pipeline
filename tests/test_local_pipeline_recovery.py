from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import textwrap
import time
import unittest

from test_validate_meeting_note import VALID_NOTE


REPO = Path(__file__).resolve().parents[1]


class LocalPipelineRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name) / "pipeline"
        shutil.copytree(REPO / "sh", self.base / "sh")
        self.reviewer = Path(self.temp.name) / "meeting-context-reviewer"
        self.reviewer.mkdir()
        self.bin = Path(self.temp.name) / "bin"
        self.bin.mkdir()
        self.valid = Path(self.temp.name) / "valid.md"
        self.valid.write_text(VALID_NOTE)
        self.trace = Path(self.temp.name) / "trace"
        self.env = os.environ.copy()
        self.env.update(MEETING_BASE_DIR=str(self.base), MEETING_PROJECTS="worxphere",
                        MEETING_CONTEXT_REVIEWER_DIR=str(self.reviewer),
                        MEETING_TRANSCRIPT_CORRECTION="0", FAKE_NOTE=str(self.valid),
                        FAKE_TRACE=str(self.trace))
        # The production entrypoint sets PATH before sourcing local config.
        (self.base / ".env").write_text("export PATH=" + shlex.quote(str(self.bin)) + ':"$PATH"\n')
        for name in ("build_employee_roster.sh", "extract_glossary.py", "build_identity_ledger.py",
                     "sync-voice-memos.sh", "import-manual-audio.sh", "prepare-audio-queue.py",
                     "transcribe.sh", "filter-low-content-transcripts.py"):
            self.script(self.base / "sh" / name, "#!/bin/sh\nexit 0\n")
        self.script(self.base / "sh/make-notes.sh", textwrap.dedent('''\
            #!/usr/bin/env python3
            import os, sys
            from pathlib import Path
            base = Path(os.environ["MEETING_BASE_DIR"])
            args = sys.argv[1:]
            only = args[args.index("--only") + 1] if "--only" in args else ""
            for transcript in (base / "transcripts/worxphere").glob("*.txt"):
                if only and only != "worxphere/" + transcript.stem:
                    continue
                note = base / "notes/worxphere" / (transcript.stem + ".md")
                if note.exists() and "--force" not in args:
                    continue
                with open(os.environ["FAKE_TRACE"], "a") as trace:
                    trace.write("note:" + transcript.stem + "\\n")
                if os.environ.get("FAKE_NOTE_FAIL"):
                    raise SystemExit(9)
                note.parent.mkdir(parents=True, exist_ok=True)
                note.write_text(Path(os.environ["FAKE_NOTE"]).read_text())
            '''))
        self.script(self.bin / "uv", textwrap.dedent('''\
            #!/usr/bin/env python3
            import json, os, sys, time
            from pathlib import Path
            args = sys.argv[1:]
            note = Path(args[args.index("--meeting") + 1])
            with open(os.environ["FAKE_TRACE"], "a") as trace:
                trace.write("review:" + note.stem + "\\n")
            if os.environ.get("FAKE_REVIEW_BARRIER"):
                barrier = Path(os.environ["FAKE_REVIEW_BARRIER"])
                (barrier / "ready").touch()
                deadline = time.monotonic() + 10
                while not (barrier / "release").exists():
                    if time.monotonic() > deadline:
                        raise SystemExit(8)
                    time.sleep(0.02)
            if os.environ.get("FAKE_REVIEW_FAIL") == "1":
                raise SystemExit(9)
            out = Path(args[args.index("--out") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "review.md").write_text("# Synthetic review\\n")
            (out / "review.json").write_text(json.dumps({"meeting": {"source_path": str(note)}}))
            if os.environ.get("FAKE_REVIEW_FAIL") != "partial":
                (out / "wiki-update-candidates.md").write_text("# No candidates\\n")
            '''))

    def tearDown(self):
        self.temp.cleanup()

    def script(self, path, text):
        path.write_text(text)
        path.chmod(0o755)

    def meeting(self, stem="20260907_100000", *, audio=True, note=False):
        for kind, suffix, content in (("transcripts", ".txt", "[00:00:00] 화자 A: 합성 회의 원문"),
                                       ("audio", ".m4a", "synthetic audio"),
                                       ("notes", ".md", VALID_NOTE)):
            if kind == "audio" and not audio or kind == "notes" and not note:
                continue
            path = self.base / kind / "worxphere" / (stem + suffix)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        return self.base / "notes/worxphere" / (stem + ".md")

    def run_pipeline(self, *args, **overrides):
        result = subprocess.run(["bash", str(self.base / "sh/run-local-pipeline.sh"), *args],
                                env={**self.env, **overrides}, text=True, capture_output=True, timeout=30)
        log = self.base / "logs/local-pipeline.log"
        result.evidence = result.stdout + result.stderr + (log.read_text() if log.exists() else "")
        return result

    def queued(self):
        path = self.base / "state/notion-publication/pending.txt"
        return path.read_text().splitlines() if path.exists() else []

    def test_failed_review_resumes_without_regenerating_note(self):
        note = self.meeting()
        first = self.run_pipeline(FAKE_REVIEW_FAIL="1")
        self.assertNotEqual(0, first.returncode, first.evidence)
        self.assertTrue(note.exists())
        self.assertEqual([], self.queued())
        note_bytes = note.read_bytes()
        second = self.run_pipeline()
        self.assertEqual(0, second.returncode, second.evidence)
        self.assertEqual(["notes/worxphere/20260907_100000.md"], self.queued())
        self.assertEqual(note_bytes, note.read_bytes())
        trace = self.trace.read_text()
        self.assertEqual(1, trace.count("note:"))
        self.assertEqual(2, trace.count("review:"))
        third = self.run_pipeline()
        self.assertEqual(0, third.returncode, third.evidence)
        self.assertEqual(trace, self.trace.read_text())

    def test_missing_reviewer_is_failure_and_remains_retryable(self):
        self.meeting()
        self.reviewer.rmdir()
        first = self.run_pipeline()
        self.assertNotEqual(0, first.returncode, first.evidence)
        self.assertEqual([], self.queued())
        self.reviewer.mkdir()
        second = self.run_pipeline()
        self.assertEqual(0, second.returncode, second.evidence)
        self.assertEqual(1, len(self.queued()))

    def test_explicit_transcript_without_audio_can_finish(self):
        note = self.meeting(audio=False)
        result = self.run_pipeline("--only", "worxphere/" + note.stem)
        self.assertEqual(0, result.returncode, result.evidence)
        self.assertTrue(note.exists(), result.evidence)
        self.assertEqual(1, len(self.queued()))

    def test_partial_reviewer_output_never_enqueues(self):
        self.meeting()
        first = self.run_pipeline(FAKE_REVIEW_FAIL="partial")
        self.assertNotEqual(0, first.returncode, first.evidence)
        self.assertEqual([], self.queued())
        second = self.run_pipeline()
        self.assertEqual(0, second.returncode, second.evidence)
        self.assertEqual(1, len(self.queued()))

    def test_only_repair_preserves_unrelated_historical_note(self):
        target = self.meeting(note=True)
        untouched = self.meeting("20260906_100000", note=True)
        before = untouched.read_bytes()
        result = self.run_pipeline("--only", "worxphere/" + target.stem)
        self.assertEqual(0, result.returncode, result.evidence)
        self.assertEqual(["notes/worxphere/" + target.name], self.queued())
        self.assertEqual(before, untouched.read_bytes())
        self.assertEqual("review:" + target.stem + "\n", self.trace.read_text())

    def test_dry_run_reports_work_without_writes(self):
        self.meeting(audio=False)
        before = sorted(str(path.relative_to(self.base)) for path in self.base.rglob("*"))
        result = self.run_pipeline("--dry-run", "--only", "worxphere/20260907_100000")
        self.assertEqual(0, result.returncode, result.evidence)
        self.assertIn("1 note/review job(s)", result.stdout)
        self.assertEqual(before, sorted(str(path.relative_to(self.base)) for path in self.base.rglob("*")))

    def test_failed_forced_generation_is_retried_as_forced(self):
        note = self.meeting(note=True)
        before = note.read_bytes()
        first = self.run_pipeline("--force-notes", FAKE_NOTE_FAIL="1")
        self.assertNotEqual(0, first.returncode, first.evidence)
        self.assertEqual(before, note.read_bytes())
        self.assertEqual([], self.queued())
        second = self.run_pipeline()
        self.assertEqual(0, second.returncode, second.evidence)
        self.assertEqual(2, self.trace.read_text().count("note:"))
        self.assertEqual(1, len(self.queued()))

    def test_resume_does_not_backfill_untracked_historical_transcripts(self):
        target = self.meeting(audio=False)
        historical = self.meeting("20260801_100000", audio=False)
        state_path = self.base / "state/local-pipeline/jobs.json"
        state_path.parent.mkdir(parents=True)
        state_path.write_text(json.dumps({"files": {
            "notes/worxphere/" + target.name: {"stage": "note", "force": False}
        }}))
        result = self.run_pipeline()
        self.assertEqual(0, result.returncode, result.evidence)
        self.assertTrue(target.exists())
        self.assertFalse(historical.exists())
        self.assertNotIn(historical.stem, self.trace.read_text())
        self.assertEqual(["notes/worxphere/" + target.name], self.queued())

    def test_only_keeps_other_pending_jobs(self):
        target = self.meeting(note=True)
        other = self.meeting("20260906_100000", note=True)
        state_path = self.base / "state/local-pipeline/jobs.json"
        state_path.parent.mkdir(parents=True)
        state_path.write_text(json.dumps({"files": {
            "notes/worxphere/" + note.name: {"stage": "review", "force": False}
            for note in (target, other)
        }}))
        result = self.run_pipeline("--only", "worxphere/" + target.stem)
        self.assertEqual(0, result.returncode, result.evidence)
        self.assertEqual(["notes/worxphere/" + other.name],
                         list(json.loads(state_path.read_text())["files"]))
        self.assertEqual(["notes/worxphere/" + target.name], self.queued())
        self.assertNotIn(other.stem, self.trace.read_text())

    def test_concurrent_entrypoint_cannot_duplicate_or_unlock_owner(self):
        self.meeting()
        barrier = Path(self.temp.name) / "barrier"
        barrier.mkdir()
        first = subprocess.Popen(["/bin/bash", str(self.base / "sh/run-local-pipeline.sh")],
                                 env={**self.env, "FAKE_REVIEW_BARRIER": str(barrier)},
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while not (barrier / "ready").exists() and first.poll() is None:
                if time.monotonic() > deadline:
                    self.fail("first pipeline did not reach review barrier")
                time.sleep(0.02)
            self.assertIsNone(first.poll())
            lock = self.base / "logs/local-pipeline.lock"
            owner = lock.read_text()
            second = self.run_pipeline()
            self.assertEqual(0, second.returncode, second.evidence)
            self.assertIn("another local pipeline instance holds the lock", second.evidence)
            self.assertEqual(owner, lock.read_text())
            self.assertEqual(1, self.trace.read_text().count("review:"))
        finally:
            (barrier / "release").touch()
            first.communicate(timeout=15)
        self.assertEqual(0, first.returncode)
        self.assertEqual(1, len(self.queued()))
        third = self.run_pipeline()
        self.assertEqual(0, third.returncode, third.evidence)
        self.assertEqual(1, self.trace.read_text().count("review:"))


if __name__ == "__main__":
    unittest.main()
