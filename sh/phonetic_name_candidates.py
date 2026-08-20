#!/usr/bin/env python3
"""Propose phonetically-close employee names for garbled STT name tokens.

The identity ledger only maps STT variants that were *already confirmed* in a past
note (exact string match). A brand-new mis-spelling of a real employee — e.g. STT
"성모/성문" for 구석모, "병사" for 고병삼 — is not in the ledger yet, so the note LLM
has to guess it against the 630-name roster on its own, inconsistently.

This script closes that gap deterministically: it decomposes Korean to jamo (pure
Unicode, no dependency), and for each name-like token in the transcript finds the
roster names whose full or given-name form is phonetically closest. The result is a
CANDIDATE hint block for the note prompt — it never rewrites the transcript; the LLM
still decides, using meeting context, whether a candidate actually applies.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
JONG = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"
HANGUL_TOKEN_RE = re.compile(r"[가-힣]{2,5}")
# Address forms strongly signal a person mention.
ADDRESSED_RE = re.compile(r"([가-힣]{2,5})(?:님|씨)")
KOREAN_SURNAMES_2 = ("남궁", "황보", "선우", "독고", "제갈", "사공", "서문", "동방")


def decompose(text: str) -> str:
    out: list[str] = []
    for ch in text:
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            out.append(CHO[code // 588])
            out.append(JUNG[(code % 588) // 28])
            jong = JONG[code % 28]
            if jong != " ":
                out.append(jong)
        else:
            out.append(ch)
    return "".join(out)


def _edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def jamo_similarity(a: str, b: str) -> float:
    ja, jb = decompose(a), decompose(b)
    if not ja or not jb:
        return 0.0
    dist = _edit_distance(ja, jb)
    return 1.0 - dist / max(len(ja), len(jb))


def given_name(full: str) -> str:
    """Drop the surname so an STT token that only heard the given name matches."""
    if len(full) >= 4 and full[:2] in KOREAN_SURNAMES_2:
        return full[2:]
    if len(full) >= 3:
        return full[1:]
    return full


def load_roster(path: Path) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip() or line.startswith("name\t"):
            continue
        parts = line.split("\t")
        name = parts[0].strip()
        dept = parts[1].strip() if len(parts) > 1 else ""
        pos = parts[2].strip() if len(parts) > 2 else ""
        status = parts[3].strip() if len(parts) > 3 else "unverified"
        if name and HANGUL_TOKEN_RE.fullmatch(name):
            rows.append((name, dept, pos, status))
    return rows


def extract_tokens(transcript: str) -> "dict[str, bool]":
    """token -> was it addressed (…님/씨). Addressed tokens rank higher."""
    tokens: dict[str, bool] = {}
    for m in ADDRESSED_RE.finditer(transcript):
        tok = re.sub(r"(?:님|씨)$", "", m.group(1)).strip()
        if len(tok) >= 2:
            tokens[tok] = True
    for m in HANGUL_TOKEN_RE.finditer(transcript):
        tok = m.group(0)
        tokens.setdefault(tok, False)
    return tokens


def best_matches(token: str, roster: list[tuple[str, str, str, str]], *, threshold: float, top: int):
    scored = []
    for name, dept, pos, status in roster:
        if token == name:
            continue  # already correct, no correction needed
        sim = max(jamo_similarity(token, name), jamo_similarity(token, given_name(name)))
        if sim >= threshold:
            scored.append((sim, name, dept, pos, status))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored[:top]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript", type=Path)
    ap.add_argument("roster", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--threshold", type=float, default=0.72,
                    help="Similarity floor for addressed (…님/씨) tokens.")
    ap.add_argument("--threshold-unaddressed", type=float, default=0.86,
                    help="Higher floor for bare tokens — most are common words, not names.")
    ap.add_argument("--max-tokens", type=int, default=30)
    ap.add_argument("--top", type=int, default=2)
    args = ap.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not args.transcript.is_file() or not args.roster.is_file():
        args.output.write_text("", encoding="utf-8")
        return 0

    transcript = args.transcript.read_text(encoding="utf-8", errors="replace")
    roster = load_roster(args.roster)
    tokens = extract_tokens(transcript)
    # Addressed tokens first, then by length (longer = more distinctive).
    ordered = sorted(tokens, key=lambda t: (not tokens[t], -len(t)))

    lines: list[str] = []
    seen: set[str] = set()
    for token in ordered:
        addressed = tokens[token]
        floor = args.threshold if addressed else args.threshold_unaddressed
        matches = best_matches(token, roster, threshold=floor, top=args.top)
        if not matches:
            continue
        # Skip tokens that exactly equal a roster name already (handled in best_matches),
        # and de-dupe identical candidate rows.
        labels = {"former": " · 퇴사", "unverified": " · 재직 미확인"}
        cand = ", ".join(
            f"{name}({dept}{', ' + pos if pos else ''}{labels.get(status, '')}) 유사도 {sim:.2f}"
            for sim, name, dept, pos, status in matches
        )
        key = f"{token}=>{cand}"
        if key in seen:
            continue
        seen.add(key)
        mark = "·호칭" if tokens[token] else ""
        lines.append(f"- `{token}`{mark} → {cand}")
        if len(lines) >= args.max_tokens:
            break

    if not lines:
        args.output.write_text("", encoding="utf-8")
        print("phonetic candidates: 0", file=sys.stderr)
        return 0

    header = [
        "발음 유사 인물 후보 (자모 기반, 로스터 대조).",
        "STT가 이름을 뭉갰을 때 발음이 가장 가까운 사내 직원 후보다. "
        "**문맥이 맞을 때만** 정정하고, 애매하면 `검증 필요`에 남긴다. "
        "자동 치환이 아니라 후보 제시일 뿐이다.",
        "`퇴사`는 과거/언급 인물 식별에만 사용하고 현재 참석자·소속·담당의 "
        "근거로 사용하지 않는다.",
        "",
    ]
    args.output.write_text("\n".join(header + lines) + "\n", encoding="utf-8")
    print(f"phonetic candidates: {len(lines)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
