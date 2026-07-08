#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import sqlite3
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DB = Path.home() / ".local/share/worxphere-data-collectors/search-index.sqlite"
DEFAULT_SOURCES = (
    "slack_message",
    "slack_canvas",
    "slack_file_decrypted",
    "notion_page",
    "gitlab_commit",
    "gitlab_merge_request",
    "gitlab_issue",
)
DEFAULT_SEEDS = (
    "NIKA",
    "니카",
    "AX 혁신추진단",
    "AI 혁신 추진단",
    "A100",
    "Claude",
    "클로드",
    "Codex",
    "코덱스",
    "팀플랜",
    "Opus",
    "오퍼스",
    "Sonnet",
    "WebSearch",
    "WDC",
    "Notion",
    "노션",
    "Slack",
    "슬랙",
    "MCP",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a small WDC evidence context for meeting note generation.")
    parser.add_argument("transcript", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--glossary-dir", type=Path)
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("WDC_INDEX_DB", "")) if os.environ.get("WDC_INDEX_DB") else DEFAULT_DB)
    parser.add_argument("--max-terms", type=int, default=int(os.environ.get("WDC_MEETING_MAX_TERMS", "10")))
    parser.add_argument("--per-source-limit", type=int, default=int(os.environ.get("WDC_MEETING_PER_SOURCE_LIMIT", "1")))
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not args.transcript.exists():
        args.output.write_text(_empty_context(f"transcript not found: {args.transcript}"), encoding="utf-8")
        return 0
    if not args.db.exists():
        args.output.write_text(_empty_context(f"WDC index database not found: {args.db}"), encoding="utf-8")
        return 0

    transcript = args.transcript.read_text(encoding="utf-8", errors="replace")
    terms = extract_terms(transcript, args.glossary_dir, max_terms=max(1, args.max_terms))
    results = search_terms(args.db, terms, per_source_limit=max(1, args.per_source_limit))
    args.output.write_text(render_context(args.db, terms, results), encoding="utf-8")
    return 0


def extract_terms(transcript: str, glossary_dir: Path | None, *, max_terms: int) -> list[str]:
    lowered = transcript.casefold()
    terms: OrderedDict[str, None] = OrderedDict()

    for seed in DEFAULT_SEEDS:
        if seed.casefold() in lowered:
            terms[seed] = None

    if glossary_dir:
        for name in ("glossary_hotwords.txt", "glossary_prompt.txt"):
            path = glossary_dir / name
            if not path.exists():
                continue
            for term in _split_candidate_terms(path.read_text(encoding="utf-8", errors="replace")):
                if len(term) >= 2 and term.casefold() in lowered:
                    terms[term] = None

    for match in re.finditer(r"\b[A-Z][A-Z0-9_-]{1,}\b", transcript):
        terms[match.group(0)] = None

    korean_suffix = r"(?:추진단|본부|부문|팀|챕터|플랫폼|에이전트|런타임|거버넌스|온보딩|메뉴판|아키텍처)"
    for match in re.finditer(rf"[가-힣A-Za-z0-9][가-힣A-Za-z0-9·/\- ]{{1,28}}{korean_suffix}", transcript):
        term = " ".join(match.group(0).split())
        if not _looks_like_person_mention(term):
            terms[term] = None

    return list(terms.keys())[:max_terms]


def _split_candidate_terms(text: str) -> list[str]:
    candidates: list[str] = []
    for raw in re.split(r"[,;\n]", text):
        term = raw.strip(" -`*[]()")
        if not term or len(term) > 60:
            continue
        if ":" in term:
            term = term.split(":", 1)[-1].strip()
        if term:
            candidates.append(term)
    return candidates


def _looks_like_person_mention(term: str) -> bool:
    return term.endswith("님") or bool(re.search(r"[가-힣]{2,4}\s*(?:대표|부문장|팀장|실장|님)", term))


def search_terms(db: Path, terms: list[str], *, per_source_limit: int) -> dict[str, list[dict[str, str]]]:
    if not terms:
        return {}
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return {term: _search_one_term(conn, term, per_source_limit=per_source_limit) for term in terms}
    finally:
        conn.close()


def _search_one_term(conn: sqlite3.Connection, term: str, *, per_source_limit: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for source in DEFAULT_SOURCES:
        rows.extend(_fts_search(conn, term, source=source, limit=per_source_limit))
    return rows


def _fts_search(conn: sqlite3.Connection, term: str, *, source: str, limit: int) -> list[dict[str, str]]:
    fts_query = f'"{term.replace(chr(34), chr(34) + chr(34))}"'
    sql = """
        SELECT d.id, d.source, d.title, d.channel_name, d.month, d.created_at, d.updated_at,
               d.path, d.url, snippet(documents_fts, 3, '[', ']', ' ... ', 12) AS snippet
        FROM documents_fts
        JOIN documents d ON d.id = documents_fts.id
        WHERE documents_fts MATCH ? AND d.source = ?
        ORDER BY rank
        LIMIT ?
    """
    try:
        rows = conn.execute(sql, (fts_query, source, limit)).fetchall()
    except sqlite3.OperationalError:
        rows = _like_search(conn, term, source=source, limit=limit)
    return [_row_to_dict(row) for row in rows]


def _like_search(conn: sqlite3.Connection, term: str, *, source: str, limit: int) -> list[sqlite3.Row]:
    like = f"%{term}%"
    return conn.execute(
        """
        SELECT d.id, d.source, d.title, d.channel_name, d.month, d.created_at, d.updated_at,
               d.path, d.url, substr(documents_fts.body, 1, 220) AS snippet
        FROM documents_fts
        JOIN documents d ON d.id = documents_fts.id
        WHERE d.source = ? AND (d.title LIKE ? OR d.channel_name LIKE ? OR documents_fts.body LIKE ?)
        ORDER BY COALESCE(d.updated_at, d.created_at, '') DESC
        LIMIT ?
        """,
        (source, like, like, like, limit),
    ).fetchall()


def _row_to_dict(row: sqlite3.Row) -> dict[str, str]:
    return {
        "id": str(row["id"]),
        "source": str(row["source"]),
        "title": str(row["title"] or ""),
        "channel": str(row["channel_name"] or ""),
        "month": str(row["month"] or ""),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "path": str(row["path"] or ""),
        "url": str(row["url"] or ""),
        "snippet": _clean_snippet(row["snippet"]),
    }


def _clean_snippet(value: object) -> str:
    return " ".join(("" if value is None else str(value)).split())


def render_context(db: Path, terms: list[str], results: dict[str, list[dict[str, str]]]) -> str:
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    lines = [
        "# WDC 회의 전 근거 컨텍스트",
        "",
        f"- 생성시각: {generated_at}",
        f"- WDC DB: `{db}`",
        "- 범위: Slack/Notion/GitLab index의 짧은 snippet과 metadata만 사용",
        "- 용도: 고유명사, 조직명, 프로젝트명, 기존 업무 연속성 보정",
        "- 주의: transcript에 없는 결정/담당/기한을 WDC만으로 만들지 말 것",
        "",
        "## 검색어",
    ]
    if terms:
        lines.extend(f"- `{term}`" for term in terms)
    else:
        lines.append("- 해당 없음")

    lines.extend(["", "## 근거 후보"])
    for term in terms:
        rows = results.get(term, [])
        lines.extend(["", f"### `{term}`"])
        if not rows:
            lines.append("- WDC hit 없음")
            continue
        sources = ", ".join(sorted({row["source"] for row in rows}))
        lines.append(f"- source presence: {sources}")
        for row in rows:
            title = row["title"] or row["path"] or row["id"]
            when = row["updated_at"] or row["created_at"] or row["month"] or "date unknown"
            location = row["url"] or row["path"] or row["channel"] or row["id"]
            snippet = row["snippet"] or "snippet 없음"
            lines.append(f"- [{row['source']}] {title} ({when})")
            lines.append(f"  - ref: `{location}`")
            lines.append(f"  - snippet: {snippet}")

    lines.extend(
        [
            "",
            "## 회의록 작성 지침",
            "- WDC 근거와 transcript가 같은 용어를 가리킬 때만 보정한다.",
            "- WDC 근거가 있어도 회의에서 명시되지 않은 결정, 담당자, 기한은 만들지 않는다.",
            "- 충돌하거나 애매한 항목은 `검증 필요`로 남긴다.",
            "- 확정 보정은 회의록 `## 11. 검증 완료`에 원문과 정정명을 함께 기록한다.",
            "",
        ]
    )
    return "\n".join(lines)


def _empty_context(reason: str) -> str:
    return "\n".join(
        [
            "# WDC 회의 전 근거 컨텍스트",
            "",
            f"- 상태: 사용 불가 ({reason})",
            "- 회의록 작성 시 WDC 근거 없이 transcript, 직원 디렉토리, 로컬 glossary만 사용한다.",
            "",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
