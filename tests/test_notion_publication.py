from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "sh" / "notion_publication.py"
SPEC = importlib.util.spec_from_file_location("notion_publication", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class NotionPublicationTests(unittest.TestCase):
    def make_base(self, root: Path) -> Path:
        base = root / "repo"
        (base / "notes" / "worxphere").mkdir(parents=True)
        (base / "glossary").mkdir(parents=True)
        (base / "glossary" / "employee_roster.tsv").write_text(
            "구석모\tAI Product팀\t팀장\n정승호\tAI Product팀\tPO\n",
            encoding="utf-8",
        )
        return base

    def write_note(self, base: Path, stem: str = "20260820_100000") -> Path:
        note = base / "notes" / "worxphere" / f"{stem}.md"
        note.write_text(
            """# 에이전트가 생성한 회의 제목

- 일시: 2026-08-20 10:00:00
- 작성 방식: AI 추정

## 1. 핵심 결론 및 결정사항
- **확정 · 결론 A** (§4.1)

## 2. Action Items
- [ ] **A1 · 결과물 작성**
  구석모 · 이번 주 · §4.1

## 3. 미결 쟁점 및 다음 결정
- **M1 · 결정 필요**
  담당자 확인 필요 · 다음 논의 전 · §4.1

## 4. 상세 논의와 근거
### 4.1 주제
- 근거 문장

## 5. 참석자·용어 검증 부록
### 5.1 참석자/언급 인물
- 참석자: 구석모, 정승호
### 5.2 검증 완료
- 해당 없음
### 5.3 검증 필요
- 해당 없음
""",
            encoding="utf-8",
        )
        return note

    def test_readable_derivative_is_date_partitioned_and_faithful(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = self.make_base(Path(temporary))
            source = self.write_note(base)
            candidate, validation = MODULE.create_readable_candidate(
                source,
                base=base,
                validator=MODULE.default_validator(),
            )
            rendered = candidate.read_text(encoding="utf-8")
            self.assertTrue(validation["faithful"])
            self.assertEqual(
                candidate,
                base
                / "notion-readable"
                / "worxphere"
                / "2026-08-20"
                / "20260820_100000.md",
            )
            self.assertIn("> - 일시: 2026-08-20 10:00:00", rendered)
            self.assertIn(
                "- [ ] **A1 · 결과물 작성** — **담당** · 구석모 · **일정** · 이번 주 · **근거** · [4.1 주제](#41-주제)",
                rendered,
            )
            self.assertIn("[4.1 주제](#41-주제)", rendered)
            self.assertGreaterEqual(rendered.count("\n---\n"), 3)
            self.assertEqual(rendered.count("결론 A"), 1)

    def test_readable_derivative_preserves_objective_fact_check_at_bottom(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = self.make_base(Path(temporary))
            source = self.write_note(base)
            source.write_text(
                source.read_text(encoding="utf-8").rstrip()
                + """

## 6. 객관 명제 팩트체크

- 범위: 객관 명제만 검토했다.
- 웹검색: 실제 실행 확인
- 결과: 객관 사실과 충돌 1건

### F1. 객관 사실과 충돌
- 발언: “1 더하기 1은 3이다.”
- 위치: 화자 A · 00:00:10
- 바로잡기: 표준 산술에서 1 + 1은 2다.
- 틀린 이유: 덧셈 규칙과 계산 결과가 다르다.
- 근거: [공식 산술 기준](https://example.org/arithmetic) — 덧셈 정의
- 확신도: 높음
""",
                encoding="utf-8",
            )
            candidate, validation = MODULE.create_readable_candidate(
                source,
                base=base,
                validator=MODULE.default_validator(),
            )
            rendered = candidate.read_text(encoding="utf-8")
            self.assertTrue(validation["faithful"])
            self.assertIn("## 6. 객관 명제 팩트체크", rendered)
            self.assertIn("https://example.org/arithmetic", rendered)
            self.assertTrue(rendered.rstrip().endswith("- 확신도: 높음"))

    def test_live_export_validation_accepts_title_stored_as_page_property(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = self.make_base(Path(temporary))
            source = self.write_note(base)
            candidate, _ = MODULE.create_readable_candidate(
                source,
                base=base,
                validator=MODULE.default_validator(),
            )
            candidate_lines = candidate.read_text(encoding="utf-8").splitlines()
            exported = base / "state" / "export.md"
            MODULE.atomic_write_text(
                exported,
                "\n".join(candidate_lines[1:]).lstrip() + "\n",
            )
            result = MODULE.validate_lossless(
                candidate,
                exported,
                MODULE.default_validator(),
                candidate_title="에이전트가 생성한 회의 제목",
            )
            self.assertTrue(result["lossless"])

    def test_live_export_validation_accepts_notion_doubled_markdown_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = self.make_base(Path(temporary))
            source = self.write_note(base)
            source.write_text(
                source.read_text(encoding="utf-8").replace(
                    "- 해당 없음\n### 5.3 검증 필요",
                    "- 화자 A~E 매핑 확인 필요\n### 5.3 검증 필요",
                ),
                encoding="utf-8",
            )
            candidate, _ = MODULE.create_readable_candidate(
                source,
                base=base,
                validator=MODULE.default_validator(),
            )
            candidate_lines = candidate.read_text(encoding="utf-8").splitlines()
            fetched_body = "\n".join(candidate_lines[1:]).lstrip() + "\n"
            fetched_body = fetched_body.replace("A~E", "A\\\\~E")
            receipt = {"fetched_markdown": fetched_body}

            result = MODULE.verify_live_receipt(
                receipt,
                source=source,
                candidate=candidate,
                base=base,
                validator=MODULE.default_validator(),
                expected_title="에이전트가 생성한 회의 제목",
                visual_assets=[],
            )

            self.assertTrue(result["lossless"])
            exported = MODULE.live_export_path_for(source, base)
            self.assertIn("A\\\\~E", exported.read_text(encoding="utf-8"))
            normalized = exported.with_name(exported.stem + "-normalized.md")
            self.assertIn("A\\~E", normalized.read_text(encoding="utf-8"))

    def test_private_keyword_must_be_standalone(self) -> None:
        self.assertTrue(MODULE.is_private_label("비공개 구석모, 정승호"))
        self.assertTrue(MODULE.is_private_label("구석모_비공개_정승호"))
        self.assertFalse(MODULE.is_private_label("비공개회의 구석모"))

    def test_external_filename_routes_and_extracts_attendees(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = self.make_base(Path(temporary))
            source = self.write_note(
                base,
                "20260820_100000_비공개_구석모_정승호",
            )
            candidate, _ = MODULE.create_readable_candidate(
                source,
                base=base,
                validator=MODULE.default_validator(),
            )
            metadata = MODULE.build_metadata(source, candidate, base=base)
            self.assertEqual(metadata.visibility, "private")
            self.assertEqual(metadata.attendees, ["구석모", "정승호"])
            self.assertEqual(metadata.title, "에이전트가 생성한 회의 제목")
            self.assertNotIn("비공개", metadata.attendees)

    def test_latest_voice_memo_title_overrides_filename_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = self.make_base(Path(temporary))
            source = self.write_note(base, "20260820_100000_비공개_구석모_정승호")
            title = (
                base
                / "state"
                / "voice-memo-titles"
                / "worxphere"
                / f"{source.stem}.txt"
            )
            title.parent.mkdir(parents=True)
            title.write_text("구석모, 정승호\n", encoding="utf-8")
            candidate, _ = MODULE.create_readable_candidate(
                source,
                base=base,
                validator=MODULE.default_validator(),
            )
            metadata = MODULE.build_metadata(source, candidate, base=base)
            self.assertEqual(metadata.visibility, "public")

    def test_visibility_sidecar_overrides_mutable_title(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = self.make_base(Path(temporary))
            source = self.write_note(base)
            visibility = (
                base
                / "state"
                / "meeting-visibility"
                / "worxphere"
                / f"{source.stem}.txt"
            )
            visibility.parent.mkdir(parents=True)
            visibility.write_text("private\n", encoding="utf-8")
            candidate, _ = MODULE.create_readable_candidate(
                source,
                base=base,
                validator=MODULE.default_validator(),
            )
            metadata = MODULE.build_metadata(source, candidate, base=base)
            self.assertEqual(metadata.visibility, "private")
            self.assertEqual(metadata.author_name, "구석모_AIProduct팀")
            self.assertEqual(
                metadata.author_user_id,
                "2a8d872b-594c-812a-a61b-0002a4cd405c",
            )

    def test_visibility_filter_requires_explicit_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = self.make_base(Path(temporary))
            source = self.write_note(base)
            _project, _stem, label = MODULE.recording_context(source, base)
            self.assertIsNone(MODULE.explicit_visibility(source, base, label))

            visibility = (
                base
                / "state"
                / "meeting-visibility"
                / "worxphere"
                / f"{source.stem}.txt"
            )
            visibility.parent.mkdir(parents=True)
            visibility.write_text("public\n", encoding="utf-8")
            self.assertEqual(
                MODULE.explicit_visibility(source, base, label),
                "public",
            )

    def test_confirmed_attendees_override_mutable_title(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = self.make_base(Path(temporary))
            source = self.write_note(base)
            title = (
                base
                / "state"
                / "voice-memo-titles"
                / "worxphere"
                / f"{source.stem}.txt"
            )
            title.parent.mkdir(parents=True)
            title.write_text("정승호\n", encoding="utf-8")
            confirmed = (
                base
                / "state"
                / "meeting-attendees"
                / "worxphere"
                / f"{source.stem}.txt"
            )
            confirmed.parent.mkdir(parents=True)
            confirmed.write_text("구석모, 정승호\n", encoding="utf-8")
            self.assertEqual(
                MODULE.resolve_attendees(source, base, "정승호"),
                ["구석모", "정승호"],
            )

    def test_enqueue_is_relative_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = self.make_base(Path(temporary))
            source = self.write_note(base)
            pending = base / "state" / "notion-publication" / "pending.txt"
            first = MODULE.enqueue(
                base=base,
                pending_file=pending,
                files=[str(source)],
            )
            second = MODULE.enqueue(
                base=base,
                pending_file=pending,
                files=[str(source)],
            )
            self.assertEqual(first, ["notes/worxphere/20260820_100000.md"])
            self.assertEqual(second, [])
            self.assertEqual(MODULE.read_pending(pending), first)

    def test_request_contains_target_specific_route_without_source_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = self.make_base(Path(temporary))
            source = self.write_note(base)
            candidate, validation = MODULE.create_readable_candidate(
                source,
                base=base,
                validator=MODULE.default_validator(),
            )
            metadata = MODULE.build_metadata(source, candidate, base=base)
            self.assertTrue(validation["faithful"])
            self.assertEqual(metadata.visibility, "public")
            self.assertEqual(
                metadata.target_data_source_id,
                MODULE.PUBLIC_DATA_SOURCE_DEFAULT,
            )
            self.assertIn("결론 A", metadata.summary)
            _metadata, _validation, request_path = MODULE.prepare(
                source,
                base=base,
                validator=MODULE.default_validator(),
                state={"files": {}},
            )
            request = json.loads(request_path.read_text(encoding="utf-8"))
            self.assertTrue(request["projection_validation"]["faithful"])
            self.assertNotIn("lossless_validation", request)

    def test_explicit_milestones_create_timeline_svg_and_embed_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = self.make_base(Path(temporary))
            source = self.write_note(base)
            text = source.read_text(encoding="utf-8")
            action_rows = """- [ ] **A1 · 결과물 작성**
  구석모 · 8월 21일 · §4.1

- [ ] **A2 · 문서 공유**
  담당자 확인 필요 · 8월 22일 · §4.1

- [ ] **A3 · 검증안 작성**
  담당자 확인 필요 · 8월 28일 · §4.1

- [ ] **A4 · 환경 준비**
  담당자 확인 필요 · 기한 확인 필요 · §4.1

- [ ] **A5 · 결과 발표**
  정승호 · 8월 28일 · §4.1"""
            text = text.replace(
                "- [ ] **A1 · 결과물 작성**\n  구석모 · 이번 주 · §4.1",
                action_rows,
            )
            source.write_text(text, encoding="utf-8")

            candidate, validation = MODULE.create_readable_candidate(
                source,
                base=base,
                validator=MODULE.default_validator(),
            )

            self.assertTrue(validation["faithful"])
            self.assertTrue(validation["visualization_included"])
            rendered = candidate.read_text(encoding="utf-8")
            self.assertEqual(validation["visualization_kind"], "milestone-timeline")
            self.assertIn("![명시 일정·마일스톤]", rendered)
            assets = MODULE.visual_assets_for(candidate)
            self.assertEqual(len(assets), 1)
            self.assertEqual(assets[0]["kind"], "milestone-timeline")
            self.assertTrue(Path(assets[0]["svg_path"]).is_file())
            self.assertTrue(Path(assets[0]["embed_html_path"]).is_file())

    def test_rollout_content_selects_rollout_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = self.make_base(Path(temporary))
            source = self.write_note(base)
            text = source.read_text(encoding="utf-8").replace(
                "- **확정 · 결론 A** (§4.1)",
                "- **방향 합의 · 공개 후 온보딩과 파일럿을 진행하고 품질 검증, 피드백 반영, 확산 순서로 운영한다.** (§4.1)",
            )
            source.write_text(text, encoding="utf-8")
            candidate, validation = MODULE.create_readable_candidate(
                source,
                base=base,
                validator=MODULE.default_validator(),
            )
            self.assertEqual(validation["visualization_kind"], "rollout-map")
            assets = MODULE.visual_assets_for(candidate)
            self.assertEqual(assets[0]["kind"], "rollout-map")

    def test_layer_content_selects_system_map(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = self.make_base(Path(temporary))
            source = self.write_note(base)
            text = source.read_text(encoding="utf-8").replace(
                "- **확정 · 결론 A** (§4.1)",
                "- **방향 합의 · AIOS Layer 1, Layer 2, Layer 3와 AI Ready Data, 니카(Nika), MCP, Skill, Marketplace, Control Plane, AX Vanguard의 연결 구조를 확정한다.** (§4.1)",
            )
            source.write_text(text, encoding="utf-8")
            candidate, validation = MODULE.create_readable_candidate(
                source,
                base=base,
                validator=MODULE.default_validator(),
            )
            self.assertEqual(validation["visualization_kind"], "system-map")
            assets = MODULE.visual_assets_for(candidate)
            self.assertEqual(assets[0]["kind"], "system-map")

    def test_multiple_independent_choices_select_decision_map(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = self.make_base(Path(temporary))
            source = self.write_note(base)
            open_rows = """- **M1 · 자동화할지 사람이 검토할지 결정 필요**
  담당자 확인 필요 · 다음 논의 전 · §4.1

- **M2 · 어느 조직이 운영할지 결정 필요**
  담당자 확인 필요 · 다음 논의 전 · §4.1

- **M3 · 기존 방식을 유지할지 전환할지 결정 필요**
  담당자 확인 필요 · 다음 논의 전 · §4.1

- **M4 · 누가 승인할지 결정 필요**
  담당자 확인 필요 · 다음 논의 전 · §4.1

- **M5 · 결과를 어떻게 검증할지 결정 필요**
  담당자 확인 필요 · 다음 논의 전 · §4.1"""
            text = source.read_text(encoding="utf-8").replace(
                "- **M1 · 결정 필요**\n  담당자 확인 필요 · 다음 논의 전 · §4.1",
                open_rows,
            )
            source.write_text(text, encoding="utf-8")
            candidate, validation = MODULE.create_readable_candidate(
                source,
                base=base,
                validator=MODULE.default_validator(),
            )
            self.assertEqual(validation["visualization_kind"], "decision-map")
            assets = MODULE.visual_assets_for(candidate)
            self.assertEqual(assets[0]["kind"], "decision-map")

    def test_render_files_does_not_enqueue_or_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = self.make_base(Path(temporary))
            source = self.write_note(base)
            results = MODULE.render_files(
                base=base,
                files=[str(source)],
                validator=MODULE.default_validator(),
            )
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0]["faithful_projection"])
            self.assertEqual(results[0]["visual_asset_count"], 0)
            self.assertFalse(
                (base / "state" / "notion-publication" / "pending.txt").exists()
            )

    def test_reconcile_requeues_a_published_note_after_title_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = self.make_base(Path(temporary))
            source = self.write_note(base)
            title = (
                base
                / "state"
                / "voice-memo-titles"
                / "worxphere"
                / f"{source.stem}.txt"
            )
            title.parent.mkdir(parents=True)
            title.write_text("구석모, 정승호\n", encoding="utf-8")
            state = base / "state" / "notion-publication" / "publications.json"
            MODULE.save_json(
                state,
                {
                    "files": {
                        "notes/worxphere/20260820_100000.md": {
                            "source_label": "비공개 구석모, 정승호",
                            "page_id": "page-id",
                        }
                    }
                },
            )
            pending = base / "state" / "notion-publication" / "pending.txt"
            added = MODULE.reconcile_changed_labels(
                base=base,
                pending_file=pending,
                state_path=state,
            )
            self.assertEqual(added, ["notes/worxphere/20260820_100000.md"])
            self.assertEqual(MODULE.read_pending(pending), added)

    def test_updated_receipt_is_success_only_after_fresh_fetch_flag(self) -> None:
        receipt = {
            "status": "updated",
            "page_id": "page-id",
            "post_fetch_verified": True,
        }
        self.assertTrue(MODULE._receipt_success(receipt))
        receipt["post_fetch_verified"] = False
        self.assertFalse(MODULE._receipt_success(receipt))


if __name__ == "__main__":
    unittest.main()
