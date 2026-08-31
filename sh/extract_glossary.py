#!/usr/bin/env python3
"""Extract a glossary of proper nouns from past meeting notes.

Writes two files consumed by transcribe.sh:
  - glossary_prompt.txt: a sentence-form prompt for --initial_prompt (context bias)
  - glossary_hotwords.txt: comma-separated terms for --hotwords (direct bias)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from collections import OrderedDict


def write_if_changed(path: Path, content: str) -> bool:
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    path.write_text(content, encoding="utf-8")
    return True


def section(text: str, header: str) -> str:
    heading_re = re.compile(
        rf"^#{{2,3}}\s*(?:[0-9]+(?:[.][0-9]+)*[.]?\s*)?{re.escape(header)}\s*$",
        re.MULTILINE,
    )
    match = heading_re.search(text)
    if not match:
        return ""
    body_start = text.find("\n", match.end())
    if body_start < 0:
        return ""
    next_heading = re.search(r"^#{2,3}\s+", text[body_start + 1 :], re.MULTILINE)
    body_end = (
        body_start + 1 + next_heading.start()
        if next_heading
        else len(text)
    )
    return text[body_start + 1 : body_end]


def extract_terms(note_text: str) -> list[str]:
    terms: list[str] = []

    meta = section(note_text, "기타 메모")
    for line in meta.splitlines():
        cat = re.match(r"\s*-\s*\*\*([^*]+?)\*\*\s*[:：]\s*(.+)", line)
        if cat:
            values = cat.group(2)
            for v in re.split(r"[,、]", values):
                v = re.sub(r"\s*\([^)]*\)\s*", " ", v)
                v = v.strip().strip(".·")
                if v:
                    terms.append(v)
        else:
            terms += re.findall(r"\*\*([^*\n]+?)\*\*", line)
            terms += re.findall(r"`([^`\n]+?)`", line)

    for header in ("검증 완료", "검증 필요"):
        sec = section(note_text, header)
        for m in re.finditer(r"→\s*\*\*([^*\n]+?)\*\*", sec):
            terms.append(m.group(1))
        for m in re.finditer(r"→\s*([A-Za-z가-힣0-9\+\&\.\-]+(?:\s[A-Za-z가-힣0-9\+\&\.\-]+){0,3})", sec):
            terms.append(m.group(1).strip())

    return terms


NOISE_KEYWORDS = (
    "추정", "근거", "원문", "예시", "불명", "또는", "추가", "참고", "이하",
    "명부", "매칭", "오기", "의심", "있음", "없음", "가능성",
)


def clean(term: str) -> str | None:
    t = term.strip().strip(".,;·「」『』\"'`")
    if t.count("(") != t.count(")"):
        t = t.replace("(", "").replace(")", "")
    t = t.strip()
    if not t or len(t) < 2 or len(t) > 40:
        return None
    if any(kw in t for kw in NOISE_KEYWORDS):
        return None
    if "/" in t or " 등" in t:
        return None
    return t


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: extract_glossary.py <notes_dir> <out_dir>", file=sys.stderr)
        sys.exit(1)

    notes_dir = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)

    dedup: OrderedDict[str, None] = OrderedDict()
    for note in sorted(notes_dir.rglob("*.md")):
        for raw in extract_terms(note.read_text()):
            cleaned = clean(raw)
            if cleaned:
                dedup[cleaned] = None

    terms = list(dedup.keys())

    prompt_path = out_dir / "glossary_prompt.txt"
    hotwords_path = out_dir / "glossary_hotwords.txt"

    limited = terms[:80]
    prompt = "회의에서 자주 언급되는 고유명사: " + ", ".join(limited) + ".\n" if terms else ""
    hotwords = ", ".join(limited) + "\n" if terms else ""
    prompt_changed = write_if_changed(prompt_path, prompt)
    hotwords_changed = write_if_changed(hotwords_path, hotwords)

    action = "updated" if prompt_changed or hotwords_changed else "unchanged"
    print(f"extracted {len(terms)} terms ({action})", file=sys.stderr)
    print(f"  -> {prompt_path}", file=sys.stderr)
    print(f"  -> {hotwords_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
