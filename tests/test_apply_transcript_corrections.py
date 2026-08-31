from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sh"))

from apply_transcript_corrections import (  # noqa: E402
    LedgerMapping,
    apply_corrections,
)
from build_identity_ledger import build_ledger, parse_variants  # noqa: E402
from check_identity_ledger_usage import IdentityMapping, iter_body_issues  # noqa: E402
from extract_glossary import extract_terms  # noqa: E402


class TranscriptCorrectionPolicyTest(unittest.TestCase):
    def apply(self, raw: str, patches: list[dict], *, ledger=None, roster=None, attendees=None):
        return apply_corrections(
            raw,
            {"version": 1, "patches": patches},
            ledger or {},
            roster or set(),
            attendees or set(),
            max_edit_ratio=0.5,
        )

    def test_accepts_ledger_backed_proper_noun_without_touching_structure(self):
        raw = "[00:00:00 - 00:00:10] A: 오늘 옥스보드 확산을 논의합니다.\n"
        patch = {
            "line": "L001",
            "before": "옥스보드",
            "after": "Worxboard",
            "category": "proper_noun",
            "confidence": 0.99,
            "reason": "ledger",
        }
        corrected, accepted, rejected = self.apply(
            raw,
            [patch],
            ledger={"옥스보드": [LedgerMapping("옥스보드", "Worxboard", 1)]},
        )
        self.assertEqual(
            corrected,
            "[00:00:00 - 00:00:10] A: 오늘 Worxboard 확산을 논의합니다.\n",
        )
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, [])

    def test_accepts_ledger_term_with_preserved_korean_particle(self):
        raw = "[00:00:00 - 00:00:10] A: 그래프레그를 우선 만들지는 않습니다.\n"
        patch = {
            "line": 1,
            "before": "그래프레그를",
            "after": "GraphRAG를",
            "category": "proper_noun",
            "confidence": 0.99,
        }
        corrected, accepted, rejected = self.apply(
            raw,
            [patch],
            ledger={"그래프레그": [LedgerMapping("그래프레그", "GraphRAG", 3)]},
        )
        self.assertIn("GraphRAG를", corrected)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, [])

    def test_rejects_low_count_person_ledger_mapping(self):
        raw = "[00:00:00 - 00:00:10] C: 승훈님께 확인해 보겠습니다.\n"
        patch = {
            "line": 1,
            "before": "승훈님",
            "after": "정승호님",
            "category": "person_name",
            "confidence": 0.99,
        }
        corrected, accepted, rejected = self.apply(
            raw,
            [patch],
            ledger={
                "승훈": [
                    LedgerMapping("승훈", "정승호(Product Chapter, 본부장)", 1)
                ]
            },
            roster={"정승호"},
            attendees={"정승호"},
        )
        self.assertEqual(corrected, raw)
        self.assertEqual(accepted, [])
        self.assertIn("no deterministic", rejected[0].reason)

    def test_accepts_phonetically_close_current_attendee(self):
        raw = "[00:00:00 - 00:00:10] A: 승호님께 먼저 여쭙겠습니다.\n"
        patch = {
            "line": 1,
            "before": "승호님",
            "after": "정승호님",
            "category": "person_name",
            "confidence": 0.96,
        }
        corrected, accepted, rejected = self.apply(
            raw,
            [patch],
            roster={"정승호"},
            attendees={"정승호"},
        )
        self.assertIn("정승호님", corrected)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, [])

    def test_accepts_ledger_person_with_honorific_and_particle(self):
        raw = "[00:00:00 - 00:00:10] A: 병사님이 먼저 설명했습니다.\n"
        patch = {
            "line": 1,
            "before": "병사님이",
            "after": "고병삼님이",
            "category": "person_name",
            "confidence": 0.99,
        }
        corrected, accepted, rejected = self.apply(
            raw,
            [patch],
            ledger={"병사": [LedgerMapping("병사", "고병삼(AI Product팀)", 10)]},
            roster={"고병삼"},
        )
        self.assertIn("고병삼님이", corrected)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, [])

    def test_rejects_number_change_even_with_ledger_evidence(self):
        raw = "[00:00:00 - 00:00:10] B: 검증은 2개월 동안 진행합니다.\n"
        patch = {
            "line": 1,
            "before": "2개월",
            "after": "3개월",
            "category": "proper_noun",
            "confidence": 0.99,
        }
        corrected, accepted, rejected = self.apply(
            raw,
            [patch],
            ledger={"2개월": [LedgerMapping("2개월", "3개월", 9)]},
        )
        self.assertEqual(corrected, raw)
        self.assertEqual(accepted, [])
        self.assertIn("protected", rejected[0].reason)

    def test_rejects_negation_change(self):
        raw = "[00:00:00 - 00:00:10] B: 이 방식은 안 됩니다.\n"
        patch = {
            "line": 1,
            "before": "안 됩니다",
            "after": "됩니다",
            "category": "proper_noun",
            "confidence": 0.99,
        }
        corrected, accepted, rejected = self.apply(raw, [patch])
        self.assertEqual(corrected, raw)
        self.assertEqual(accepted, [])
        self.assertIn("protected", rejected[0].reason)

    def test_rejects_sentence_rewrite(self):
        raw = "[00:00:00 - 00:00:10] B: 이 문장은 인식이 많이 무너진 긴 발화입니다.\n"
        patch = {
            "line": 1,
            "before": "이 문장은 인식이 많이 무너진 긴 발화입니다",
            "after": "로드맵을 다음 주까지 다시 작성하기로 했습니다",
            "category": "proper_noun",
            "confidence": 0.99,
        }
        corrected, accepted, rejected = self.apply(raw, [patch])
        self.assertEqual(corrected, raw)
        self.assertEqual(accepted, [])
        self.assertTrue(
            "sentence rewriting" in rejected[0].reason
            or "too much" in rejected[0].reason
        )

    def test_rejects_expanding_an_already_canonical_acronym(self):
        raw = "[00:00:00 - 00:00:10] B: TA 방향을 먼저 정리합니다.\n"
        patch = {
            "line": 1,
            "before": "TA",
            "after": "Talent Agent(TA)",
            "category": "acronym",
            "confidence": 0.99,
        }
        corrected, accepted, rejected = self.apply(
            raw,
            [patch],
            ledger={"TA": [LedgerMapping("TA", "Talent Agent(TA)", 9)]},
        )
        self.assertEqual(corrected, raw)
        self.assertEqual(accepted, [])
        self.assertIn("already-canonical", rejected[0].reason)

    def test_rejects_expanding_acronym_with_preserved_particle(self):
        raw = "[00:00:00 - 00:00:10] B: TA는 다음 주에 정리합니다.\n"
        patch = {
            "line": 1,
            "before": "TA는",
            "after": "Talent Agent(TA)는",
            "category": "acronym",
            "confidence": 0.99,
        }
        corrected, accepted, rejected = self.apply(
            raw,
            [patch],
            ledger={"TA": [LedgerMapping("TA", "Talent Agent(TA)", 9)]},
        )
        self.assertEqual(corrected, raw)
        self.assertEqual(accepted, [])
        self.assertIn("already-canonical", rejected[0].reason)

    def test_rejects_overlapping_patches(self):
        raw = "[00:00:00 - 00:00:10] A: 옥스보드를 확인합니다.\n"
        patches = [
            {
                "line": 1,
                "before": "옥스보드",
                "after": "Worxboard",
                "category": "proper_noun",
                "confidence": 0.99,
            },
            {
                "line": 1,
                "before": "옥스보드",
                "after": "Worxboard",
                "category": "proper_noun",
                "confidence": 0.98,
            },
        ]
        corrected, accepted, rejected = self.apply(
            raw,
            patches,
            ledger={"옥스보드": [LedgerMapping("옥스보드", "Worxboard", 1)]},
        )
        self.assertIn("Worxboard", corrected)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected[-1].reason, "overlapping patch")

    def test_ledger_variant_parser_handles_comma_separated_backticks(self):
        self.assertEqual(
            parse_variants("`웍스무드`, `옥스보드` / `웍스보드님`"),
            ["웍스무드", "옥스보드", "웍스보드"],
        )

    def test_ledger_can_exclude_current_and_future_notes(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            notes = root / "notes"
            transcripts = root / "transcripts"
            notes.mkdir()
            transcripts.mkdir()
            template = "## 11. 검증 완료\n- `옥스보드` -> **Worxboard** (근거)\n"
            for name in ("20260714_100000", "20260715_160933", "20260716_100000"):
                (notes / f"{name}.md").write_text(template, encoding="utf-8")
                (transcripts / f"{name}.txt").write_text("옥스보드\n", encoding="utf-8")
            ledger = build_ledger(
                notes,
                transcripts,
                before_name="20260715_160933.md",
            )
            self.assertEqual(ledger["Worxboard"].variants["옥스보드"], 1)

    def test_ledger_reads_new_appendix_subsection(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            notes = root / "notes"
            transcripts = root / "transcripts"
            notes.mkdir()
            transcripts.mkdir()
            name = "20260818_100000"
            (notes / f"{name}.md").write_text(
                "## 5. 참석자·용어 검증 부록\n"
                "### 5.2 검증 완료\n"
                "- `옥스보드` -> **Worxboard** (근거)\n"
                "### 5.3 검증 필요\n- 해당 없음\n",
                encoding="utf-8",
            )
            (transcripts / f"{name}.txt").write_text("옥스보드\n", encoding="utf-8")
            ledger = build_ledger(notes, transcripts)
            self.assertEqual(ledger["Worxboard"].variants["옥스보드"], 1)

    def test_glossary_reads_new_appendix_subsections(self):
        terms = extract_terms(
            "## 5. 참석자·용어 검증 부록\n"
            "### 5.2 검증 완료\n"
            "- `옥스보드` → **Worxboard** (근거)\n"
            "### 5.3 검증 필요\n"
            "- `클로도` → **Claude** 확인 필요\n"
        )
        self.assertIn("Worxboard", terms)
        self.assertIn("Claude", terms)

    def test_identity_usage_skips_new_completed_verification_subsection(self):
        mapping = IdentityMapping(variant="성모", target="구석모", count=7)
        note = (
            "## 5. 참석자·용어 검증 부록\n"
            "### 5.2 검증 완료\n"
            "- `성모` → **구석모** (근거)\n"
            "### 5.3 검증 필요\n- 해당 없음\n"
        )
        self.assertEqual([], list(iter_body_issues(note, [mapping])))


if __name__ == "__main__":
    unittest.main()
