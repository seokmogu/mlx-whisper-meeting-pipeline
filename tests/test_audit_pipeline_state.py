from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "sh" / "audit_pipeline_state.py"
sys.path.insert(0, str(REPO / "sh"))
SPEC = importlib.util.spec_from_file_location("audit_pipeline_state", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
NOTION = sys.modules["notion_publication"]


VALID_NOTE = """# 비밀 회의 제목

- 일시: 2026-09-07 10:00:00

## 1. 핵심 결론 및 결정사항
- **확정 · 결론 A** (§4.1)

## 2. Action Items
- [ ] **A1 · 결과물 작성**
  홍길동 · 이번 주 · §4.1

## 3. 미결 쟁점 및 다음 결정
- **M1 · 결정 필요**
  담당자 확인 필요 · 다음 논의 전 · §4.1

## 4. 상세 논의와 근거
### 4.1 주제
- 근거 문장

## 5. 참석자·용어 검증 부록
### 5.1 참석자/언급 인물
- 참석자: 홍길동
### 5.2 검증 완료
- 해당 없음
### 5.3 검증 필요
- 해당 없음
"""


class AuditPipelineStateTest(unittest.TestCase):
    def make_base(self, root: Path) -> tuple[Path, Path]:
        base = root / "repo"
        note = base / "notes" / "worxphere" / "secret-meeting.md"
        note.parent.mkdir(parents=True)
        note.write_text(VALID_NOTE, encoding="utf-8")
        (base / "glossary").mkdir()
        (base / "glossary" / "employee_roster.tsv").write_text(
            "홍길동\tAI Product팀\tPO\n", encoding="utf-8"
        )
        return base, note

    def make_ready(self, base: Path, note: Path) -> None:
        transcript = base / "transcripts" / "worxphere" / "secret-meeting.txt"
        transcript.parent.mkdir(parents=True)
        transcript.write_text("00:00:00 화자 A: 비공개 논의\n", encoding="utf-8")
        candidate, _ = NOTION.create_readable_candidate(
            note,
            base=base,
            validator=NOTION.default_validator(),
        )
        review_dir = NOTION.review_dir_for(note, base)
        review_dir.mkdir(parents=True)
        (review_dir / "review.md").write_text("review\n", encoding="utf-8")
        (review_dir / "review.json").write_text("{}\n", encoding="utf-8")
        (review_dir / "wiki-update-candidates.md").write_text(
            "candidate\n", encoding="utf-8"
        )
        NOTION.save_json(
            NOTION.readiness_path_for(note, base),
            {
                "schema_version": 1,
                "source_path": str(note.resolve()),
                "source_sha256": NOTION.sha256_file(note),
                "transcript_path": str(transcript.resolve()),
                "transcript_sha256": NOTION.sha256_file(transcript),
                "readable_path": str(candidate.resolve()),
                "readable_sha256": NOTION.sha256_file(candidate),
                "review_dir": str(review_dir.resolve()),
                "review_artifacts": {
                    name: NOTION.sha256_file(review_dir / name)
                    for name in MODULE.REVIEW_ARTIFACTS
                },
            },
        )

    def snapshot(self, base: Path) -> dict[str, tuple[int, str]]:
        result: dict[str, tuple[int, str]] = {}
        for path in base.rglob("*"):
            if path.is_file():
                result[str(path.relative_to(base))] = (
                    path.stat().st_mtime_ns,
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
        return result

    def run_cli(self, base: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--base", str(base), *extra],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_healthy_report_is_opaque_and_does_not_mutate_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base, note = self.make_base(Path(temporary))
            self.make_ready(base, note)
            before = self.snapshot(base)
            result = self.run_cli(base)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "healthy")
            self.assertEqual(report["summary"]["note_count"], 1)
            public = json.dumps(report, ensure_ascii=False)
            for secret in ("비밀 회의 제목", "홍길동", "secret-meeting", str(base)):
                self.assertNotIn(secret, public)
            self.assertEqual(before, self.snapshot(base))

    def test_reports_each_required_gate_without_leaking_note_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base, note = self.make_base(Path(temporary))
            report, code, _plan = MODULE.audit_state(base)
            self.assertEqual(code, 1)
            self.assertTrue(
                {
                    "PREVIEW_MISSING",
                    "READINESS_MISSING",
                    "REVIEW_MISSING",
                    "TRANSCRIPT_MISSING",
                }
                <= set(report["notes"][0]["issue_codes"])
            )

            self.make_ready(base, note)
            note.write_text(note.read_text(encoding="utf-8").replace("결론 A", "결론 B"), encoding="utf-8")
            NOTION.create_readable_candidate(note, base=base, validator=NOTION.default_validator())
            report, code, _plan = MODULE.audit_state(base)
            self.assertEqual(code, 1)
            self.assertIn("READINESS_STALE", report["notes"][0]["issue_codes"])

            note.write_text("# broken\n", encoding="utf-8")
            report, code, _plan = MODULE.audit_state(base)
            self.assertEqual(code, 1)
            self.assertIn("NOTE_INVALID", report["notes"][0]["issue_codes"])

    def test_orphan_queue_and_staged_job_are_actionable_with_opaque_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base, note = self.make_base(Path(temporary))
            self.make_ready(base, note)
            pending = base / "state" / "notion-publication" / "pending.txt"
            pending.parent.mkdir(parents=True, exist_ok=True)
            pending.write_text("notes/worxphere/missing.md\n", encoding="utf-8")
            jobs = base / "state" / "local-pipeline" / "jobs.json"
            jobs.parent.mkdir(parents=True)
            jobs.write_text(
                json.dumps({"files": {"notes/worxphere/secret-meeting.md": {"stage": "review"}}}),
                encoding="utf-8",
            )
            report, code, plan = MODULE.audit_state(base)
            self.assertEqual(code, 1)
            codes = {code for item in report["notes"] for code in item["issue_codes"]}
            self.assertTrue({"QUEUE_ORPHAN_REF", "JOB_STAGED_REVIEW"} <= codes)
            public = json.dumps(report, ensure_ascii=False)
            self.assertNotIn("secret-meeting", public)
            self.assertEqual(len(plan), 2)

    def test_malformed_readiness_state_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base, note = self.make_base(Path(temporary))
            self.make_ready(base, note)
            NOTION.readiness_path_for(note, base).write_text("{not json\n", encoding="utf-8")
            report, code, _plan = MODULE.audit_state(base)
            self.assertEqual(code, 2)
            self.assertEqual(report["status"], "invalid")
            self.assertEqual(report["global_issue_codes"], ["READINESS_INVALID"])
            NOTION.readiness_path_for(note, base).write_text("{}\n", encoding="utf-8")
            report, code, _plan = MODULE.audit_state(base)
            self.assertEqual(code, 2)
            self.assertEqual(report["global_issue_codes"], ["READINESS_INVALID"])

    def test_corrupt_queue_fails_closed_and_private_plan_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base, note = self.make_base(Path(temporary))
            self.make_ready(base, note)
            pending = base / "state" / "notion-publication" / "pending.txt"
            pending.parent.mkdir(parents=True, exist_ok=True)
            pending.write_text("../../outside.md\n", encoding="utf-8")
            public_path = Path(temporary) / "public.json"
            private_path = Path(temporary) / "private-plan.json"
            result = self.run_cli(
                base,
                "--output",
                str(public_path),
                "--private-plan",
                str(private_path),
            )
            self.assertEqual(result.returncode, 2)
            report = json.loads(public_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "invalid")
            self.assertEqual(report["global_issue_codes"], ["STATE_CORRUPT"])
            private = json.loads(private_path.read_text(encoding="utf-8"))
            self.assertEqual(private["kind"], "local recovery plan only; not executable")

    def test_output_cannot_alias_an_input_or_existing_hardlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base, note = self.make_base(root)
            self.make_ready(base, note)
            pending = base / "state" / "notion-publication" / "pending.txt"
            pending.write_text("# protected input\n", encoding="utf-8")
            before = self.snapshot(base)
            result = self.run_cli(base, "--output", str(pending))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["global_issue_codes"], ["OUTPUT_PATH_INVALID"])
            self.assertEqual(before, self.snapshot(base))

            alias = root / "hardlink.json"
            os.link(note, alias)
            result = self.run_cli(base, "--output", str(alias))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(before, self.snapshot(base))

    def test_invalid_base_and_symlink_note_reference_are_structured_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report, code, _plan = MODULE.audit_state(root / "missing")
            self.assertEqual(code, 2)
            self.assertEqual(report["global_issue_codes"], ["BASE_INVALID"])

            base, _note = self.make_base(root)
            external = root / "external.md"
            external.write_text(VALID_NOTE, encoding="utf-8")
            escaped = base / "notes" / "worxphere" / "escaped.md"
            escaped.symlink_to(external)
            report, code, _plan = MODULE.audit_state(base)
            self.assertEqual(code, 2)
            self.assertIn("NOTE_REFERENCE_INVALID", report["global_issue_codes"])

    def test_private_plan_actions_do_not_offer_unsafe_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base, note = self.make_base(Path(temporary))
            self.make_ready(base, note)
            note.write_text("# broken\n", encoding="utf-8")
            _report, _code, plan = MODULE.audit_state(base)
            self.assertEqual(plan[0]["action"], "regenerate_invalid_note")
            self.assertEqual(
                plan[0]["proposed_run_local_args"][:2], ["--force-notes", "--only"]
            )

            transcript = base / "transcripts" / "worxphere" / "secret-meeting.txt"
            transcript.unlink()
            _report, _code, plan = MODULE.audit_state(base)
            self.assertEqual(plan[0]["action"], "restore_source_required")
            self.assertEqual(plan[0]["proposed_run_local_args"], [])

    def test_corrupt_publication_tracking_is_invalid_and_count_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base, note = self.make_base(Path(temporary))
            self.make_ready(base, note)
            state = base / "state" / "notion-publication" / "publications.json"
            state.write_text("{bad json\n", encoding="utf-8")
            report, code, _plan = MODULE.audit_state(base)
            self.assertEqual(code, 2)
            self.assertIn("PUBLICATION_STATE_CORRUPT", report["global_issue_codes"])
            self.assertIsNone(report["summary"]["published_tracking_count"])

    def test_fresh_project_assessment_outputs_are_allowed_and_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base, note = self.make_base(Path(temporary))
            self.make_ready(base, note)
            out_dir = base / "state/assessments/example"
            out_dir.mkdir(parents=True)
            output = out_dir / "audit.json"
            plan = out_dir / "plan.json"
            result = self.run_cli(base, "--output", str(output), "--private-plan", str(plan))
            self.assertEqual(0, result.returncode, result.stderr)
            before = output.read_bytes()
            result = self.run_cli(base, "--output", str(output))
            self.assertEqual(2, result.returncode)
            self.assertEqual(before, output.read_bytes())

    def test_whitespace_transcript_does_not_report_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base, note = self.make_base(Path(temporary))
            self.make_ready(base, note)
            transcript = base / "transcripts/worxphere/secret-meeting.txt"
            transcript.write_text("  \n\t\n")
            report, code, plan = MODULE.audit_state(base)
            self.assertEqual(1, code)
            self.assertIn("TRANSCRIPT_MISSING", report["notes"][0]["issue_codes"])
            self.assertEqual("restore_source_required", plan[0]["action"])

    def test_missing_note_with_valid_source_can_be_generated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base, note = self.make_base(Path(temporary))
            self.make_ready(base, note)
            pending = base / "state/notion-publication/pending.txt"
            pending.write_text("notes/worxphere/secret-meeting.md\n")
            note.unlink()
            _report, code, plan = MODULE.audit_state(base)
            self.assertEqual(1, code)
            self.assertEqual("generate_missing_note", plan[0]["action"])
            self.assertEqual(["--only", "worxphere/secret-meeting"], plan[0]["proposed_run_local_args"])

    def test_legacy_unscoped_publication_is_distinct_from_corrupt_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base, note = self.make_base(Path(temporary))
            self.make_ready(base, note)
            state = base / "state/notion-publication/publications.json"
            state.write_text(json.dumps({"files": {
                "notes/worxphere/secret-meeting.md": {},
                "meeting-note-rebuilds/old/notes/worxphere/synthetic.md": {}
            }}))
            report, code, _plan = MODULE.audit_state(base)
            self.assertEqual(1, code)
            self.assertEqual("actionable", report["status"])
            self.assertEqual(["PUBLICATION_REFERENCE_UNSCOPED"], report["global_issue_codes"])
            self.assertEqual(1, report["summary"]["published_tracking_count"])
            self.assertEqual(1, report["summary"]["published_unscoped_count"])

    def test_pending_gaps_are_separate_from_historical_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base, note = self.make_base(Path(temporary))
            self.make_ready(base, note)
            (note.parent / "older.md").write_text(VALID_NOTE)
            pending = base / "state/notion-publication/pending.txt"
            pending.write_text("notes/worxphere/secret-meeting.md\n")
            report, code, _plan = MODULE.audit_state(base)
            self.assertEqual(1, code)
            self.assertEqual(1, report["summary"]["actionable_count"])
            self.assertEqual(0, report["summary"]["pending_actionable_count"])
            self.assertEqual({}, report["pending_issue_counts"])


if __name__ == "__main__":
    unittest.main()
