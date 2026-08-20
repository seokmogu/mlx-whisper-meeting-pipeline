#!/usr/bin/env python3
"""Accumulate confirmed STT-correction mappings from all past meeting notes.

Each note's ``## 11. 검증 완료`` section records confirmed resolutions in the form::

    - `이비타/이디따/에비타` -> **EBITDA** (근거)
    - `성모님 / 성문님` (A 화자) → **구석모(AI Product팀, 팀장) 추정** (근거)

The note LLM re-derives these every meeting because they never persist. This script
scans every note, extracts the ``변형 → 정정`` pairs, deduplicates, counts how many
meetings confirmed each mapping (higher = more reliable), and writes a compact ledger
that ``make-notes.sh`` injects into the next note-generation prompt.

The ledger is REFERENCE material for the LLM, not a blind find-replace table: person
names are often estimates and some source tokens collide with real words, so the LLM
applies each mapping only when the transcript context agrees.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LedgerEntry:
    # variant -> number of distinct meetings that confirmed THAT variant.
    variants: "OrderedDict[str, int]" = field(default_factory=OrderedDict)

# A 검증 완료 bullet: one or more source variants before an arrow (-> or →), then
# the canonical form in the first bold span. Capturing the whole source side keeps
# comma-separated forms such as `` `웍스무드`, `옥스보드` -> **Worxboard** ``.
LEDGER_LINE_RE = re.compile(
    r"^\s*[-*]\s*(.+?)\s*(?:->|→)\s*\*\*([^*]+)\*\*",
)
SECTION_HEADER_RE = re.compile(r"^##\s")
CONFIRMED_HEADER_RE = re.compile(r"^##\s*(?:[0-9]+[.]\s*)?검증\s*완료")
# Trailing hedges/annotations make a target ineligible for the confirmed ledger.
# A note can place a candidate in `검증 완료`, but `추정/후보/확인 필요` still means
# it must not become a cross-meeting high-confidence correction.
TARGET_HEDGE_RE = re.compile(r"(?:\s*(?:추정|확인 필요|검증 필요|후보|가능성|잠정))+\s*$")
SPEAKER_ANNOTATION_RE = re.compile(r"\s*\([^)]*화자[^)]*\)\s*")


def iter_confirmed_lines(note_text: str):
    in_section = False
    for line in note_text.splitlines():
        if SECTION_HEADER_RE.match(line):
            in_section = bool(CONFIRMED_HEADER_RE.match(line))
            continue
        if in_section:
            yield line


def parse_variants(raw_source: str) -> list[str]:
    cleaned = SPEAKER_ANNOTATION_RE.sub(" ", raw_source)
    variants: list[str] = []
    for piece in re.split(r"\s*(?:/|,|·)\s*", cleaned):
        piece = piece.strip().strip("`").strip()
        # Drop trailing honorifics so "성모님" and "성모" collapse.
        piece = re.sub(r"(?:님|씨)$", "", piece).strip()
        if piece and len(piece) <= 40:
            variants.append(piece)
    return variants


def normalize_target(raw_target: str) -> str:
    target = raw_target.strip()
    if TARGET_HEDGE_RE.search(target):
        return ""
    # Reject sentence-fragment targets: a clean canonical name/term never opens
    # with a quote mark or runs to a full clause. These come from 검증 완료 lines
    # whose arrow sits mid-sentence rather than in a proper `변형` → **정정** shape.
    if target[:1] in {'"', "'", "“", "‘"} or len(target) > 45:
        return ""
    return target


def _load_transcript(note: Path, notes_dir: Path, transcripts_dir: Path | None) -> str | None:
    if transcripts_dir is None:
        return None
    try:
        rel = note.relative_to(notes_dir).with_suffix(".txt")
    except ValueError:
        return None
    path = transcripts_dir / rel
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def build_ledger(
    notes_dir: Path,
    transcripts_dir: Path | None = None,
    before_name: str | None = None,
) -> "OrderedDict[str, LedgerEntry]":
    ledger: "OrderedDict[str, LedgerEntry]" = OrderedDict()
    for note in sorted(notes_dir.rglob("*.md")):
        if before_name is not None and note.name >= before_name:
            continue
        try:
            text = note.read_text(encoding="utf-8")
        except OSError:
            continue
        # Ground each confirmation on the meeting's OWN transcript: only count a
        # variant that actually appears in that meeting's STT output. This stops the
        # ledger from self-reinforcing a mapping that a note merely echoed from the
        # injected ledger/context rather than from this meeting's audio. When the
        # transcript is missing we fall back to counting (backward compatible).
        transcript_text = _load_transcript(note, notes_dir, transcripts_dir)
        seen_in_note: set[tuple[str, str]] = set()
        for line in iter_confirmed_lines(text):
            m = LEDGER_LINE_RE.match(line)
            if not m:
                continue
            target = normalize_target(m.group(2))
            if not target:
                continue
            for variant in parse_variants(m.group(1)):
                if variant == target:
                    continue
                if transcript_text is not None and variant not in transcript_text:
                    continue
                key = (target, variant)
                if key in seen_in_note:
                    continue
                seen_in_note.add(key)
                entry = ledger.setdefault(target, LedgerEntry())
                # Count each variant by how many distinct meetings confirmed it,
                # NOT by the target's aggregate variant count — otherwise a target
                # with many spellings inflates every variant's confidence.
                entry.variants[variant] = entry.variants.get(variant, 0) + 1
    return ledger


def render_ledger(ledger: "OrderedDict[str, LedgerEntry]", min_count: int, max_entries: int) -> str:
    # One row per (variant, target); each variant's count is its own meeting-confirmation
    # count, so ranking and the downstream min-count gate are per-variant and meaningful.
    rows = [
        (variant, target, count)
        for target, entry in ledger.items()
        for variant, count in entry.variants.items()
        if count >= min_count
    ]
    # Most-confirmed first; these are the highest-confidence mappings.
    rows.sort(key=lambda r: (-r[2], r[1], r[0]))
    rows = rows[:max_entries]
    if not rows:
        return ""
    lines = [
        "누적 확정 표기 사전 (과거 회의록 `검증 완료`에서 축적).",
        "형식: `STT 변형 후보` → 정정 (확정 횟수). 전사 문맥이 맞을 때만 적용하고, 애매하면 검증 필요에 남긴다.",
        "",
    ]
    for variant, target, count in rows:
        lines.append(f"- {variant} → **{target}** ({count}회)")
    return "\n".join(lines) + "\n"


def write_if_changed(path: Path, content: str) -> bool:
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    path.write_text(content, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("notes_dir", type=Path)
    parser.add_argument("out_file", type=Path)
    parser.add_argument("--min-count", type=int, default=1,
                        help="Only include mappings confirmed in at least this many meetings.")
    parser.add_argument("--max-entries", type=int, default=200)
    parser.add_argument(
        "--before-name",
        help="Only use note basenames lexically earlier than this value (for rerun-safe context).",
    )
    parser.add_argument("--transcripts-dir", type=Path, default=None,
                        help="Transcript root (mirrors notes/). Defaults to a sibling 'transcripts' dir. "
                             "A variant is counted only if it appears in the meeting's own transcript.")
    args = parser.parse_args()

    if not args.notes_dir.is_dir():
        print(f"notes dir not found: {args.notes_dir}", file=sys.stderr)
        return 0

    transcripts_dir = args.transcripts_dir
    if transcripts_dir is None:
        sibling = args.notes_dir.parent / "transcripts"
        transcripts_dir = sibling if sibling.is_dir() else None

    ledger = build_ledger(args.notes_dir, transcripts_dir, before_name=args.before_name)
    rendered = render_ledger(ledger, min_count=max(1, args.min_count), max_entries=args.max_entries)
    args.out_file.parent.mkdir(parents=True, exist_ok=True)
    changed = write_if_changed(args.out_file, rendered)

    total = sum(1 for e in ledger.values() for c in e.variants.values() if c >= max(1, args.min_count))
    action = "updated" if changed else "unchanged"
    print(f"identity ledger: {total} confirmed mappings ({action}) -> {args.out_file}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
