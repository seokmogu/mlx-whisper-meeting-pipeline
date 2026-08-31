#!/usr/bin/env python3
"""Fail a meeting note when high-confidence identity variants leak into body text."""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


LEDGER_LINE_RE = re.compile(r"^\s*[-*]\s*(.+?)\s*(?:->|→)\s*\*\*([^*]+)\*\*.*?\((\d+)회\)")
SECTION_HEADER_RE = re.compile(
    r"^#{2,3}\s*(?:[0-9]+(?:[.][0-9]+)*[.]?\s*)?(.+?)\s*$"
)
HANGUL_TOKEN_RE = re.compile(r"^[가-힣]{2,8}$")
KOREAN_PARTICLE_RE = r"(?:은|는|이|가|을|를|과|와|에게|께|도|만|로|으로|의)"


@dataclass(frozen=True)
class IdentityMapping:
    variant: str
    target: str
    count: int


def load_roster_names(path: Path | None) -> set[str]:
    if path is None or not path.is_file():
        return set()
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("name\t"):
            continue
        names.add(line.split("\t", 1)[0].strip())
    return names


def target_name(target: str) -> str:
    return re.split(r"[\s(]", target.strip(), maxsplit=1)[0]


def parse_variants(raw: str) -> list[str]:
    variants: list[str] = []
    for piece in raw.split(","):
        value = piece.strip().strip("`").strip()
        value = re.sub(r"(?:님|씨)$", "", value).strip()
        if HANGUL_TOKEN_RE.match(value):
            variants.append(value)
    return variants


def load_mappings(ledger_path: Path, roster_names: set[str], min_count: int) -> list[IdentityMapping]:
    mappings: list[IdentityMapping] = []
    if not ledger_path.is_file():
        return mappings
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        match = LEDGER_LINE_RE.match(line)
        if not match:
            continue
        count = int(match.group(3))
        if count < min_count:
            continue
        target = match.group(2).strip()
        name = target_name(target)
        if roster_names and name not in roster_names:
            continue
        for variant in parse_variants(match.group(1)):
            if variant != name:
                mappings.append(IdentityMapping(variant=variant, target=target, count=count))
    return mappings


def iter_body_issues(note_text: str, mappings: list[IdentityMapping]):
    current_section = ""
    for lineno, line in enumerate(note_text.splitlines(), start=1):
        header = SECTION_HEADER_RE.match(line)
        if header:
            current_section = header.group(1).strip()
        if current_section == "검증 완료":
            continue
        for mapping in mappings:
            pattern = re.compile(
                rf"(?<![가-힣A-Za-z0-9])"
                rf"{re.escape(mapping.variant)}(?:님|씨)?"
                rf"(?=$|[^가-힣A-Za-z0-9]|{KOREAN_PARTICLE_RE})",
            )
            if pattern.search(line):
                yield lineno, line.strip(), mapping


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("note", type=Path)
    parser.add_argument("ledger", type=Path)
    parser.add_argument("--roster", type=Path)
    parser.add_argument("--min-count", type=int, default=7)
    args = parser.parse_args()

    roster_names = load_roster_names(args.roster)
    mappings = load_mappings(args.ledger, roster_names, max(1, args.min_count))
    if not mappings:
        return 0

    try:
        note_text = args.note.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"identity ledger usage check failed to read note: {exc}", file=sys.stderr)
        return 1

    issues = list(iter_body_issues(note_text, mappings))
    if not issues:
        return 0

    print("identity ledger usage check failed: high-confidence variants remain outside 검증 완료", file=sys.stderr)
    for lineno, line, mapping in issues[:20]:
        print(
            f"- line {lineno}: `{mapping.variant}` should be `{mapping.target}` "
            f"({mapping.count}회) :: {line}",
            file=sys.stderr,
        )
    if len(issues) > 20:
        print(f"- ... {len(issues) - 20} more", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
