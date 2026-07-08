#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
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

# Ubiquitous tool/platform terms: useful for term-correction lookups, but they
# appear in almost every meeting, so they must NOT drive related-meeting ranking
# (they'd flood it with generic dailies instead of topically-relevant meetings).
GENERIC_MEETING_TERMS = frozenset(
    {
        "claude", "클로드", "codex", "코덱스", "opus", "오퍼스", "sonnet",
        "websearch", "notion", "노션", "slack", "슬랙", "mcp", "wdc",
        "action items", "action item", "weekly", "daily", "미팅", "회의",
    }
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a small WDC evidence context for meeting note generation.")
    parser.add_argument("transcript", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--glossary-dir", type=Path)
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("WDC_INDEX_DB", "")) if os.environ.get("WDC_INDEX_DB") else DEFAULT_DB)
    parser.add_argument("--max-terms", type=int, default=int(os.environ.get("WDC_MEETING_MAX_TERMS", "10")))
    parser.add_argument("--per-source-limit", type=int, default=int(os.environ.get("WDC_MEETING_PER_SOURCE_LIMIT", "1")))
    parser.add_argument("--related-limit", type=int, default=int(os.environ.get("WDC_RELATED_MEETINGS_LIMIT", "5")),
                        help="Max related company meetings (from meeting_notes) to surface as keyword labels.")
    parser.add_argument("--related-months", type=int, default=int(os.environ.get("WDC_RELATED_MEETINGS_MONTHS", "6")),
                        help="Only consider related meetings within this many months.")
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
    # Own teams (owner_team values) that may include a couple of short decision lines;
    # everyone else is keyword-only to prevent cross-meeting content contamination.
    own_teams = _split_env_list(os.environ.get("WDC_OWN_TEAMS", ""))
    related = search_related_meetings(
        args.db, terms, limit=max(0, args.related_limit), months_back=max(1, args.related_months), own_teams=own_teams
    )
    confirmed_names = load_confirmed_names(args.glossary_dir)
    args.output.write_text(
        render_context(args.db, terms, results, related=related, confirmed_names=confirmed_names), encoding="utf-8"
    )
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
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    conn.row_factory = sqlite3.Row
    try:
        return {term: _search_one_term(conn, term, per_source_limit=per_source_limit) for term in terms}
    except sqlite3.Error:
        return {}
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
    like = f"%{_like_escape(term)}%"
    return conn.execute(
        r"""
        SELECT d.id, d.source, d.title, d.channel_name, d.month, d.created_at, d.updated_at,
               d.path, d.url, substr(documents_fts.body, 1, 220) AS snippet
        FROM documents_fts
        JOIN documents d ON d.id = documents_fts.id
        WHERE d.source = ? AND (d.title LIKE ? ESCAPE '\' OR d.channel_name LIKE ? ESCAPE '\' OR documents_fts.body LIKE ? ESCAPE '\')
        ORDER BY COALESCE(d.updated_at, d.created_at, '') DESC
        LIMIT ?
        """,
        (source, like, like, like, limit),
    ).fetchall()


def _row_to_dict(row: sqlite3.Row) -> dict[str, str]:
    return {
        "id": str(row["id"]),
        "source": str(row["source"]),
        "title": _inline(row["title"] or "", max_len=200),
        "channel": _inline(row["channel_name"] or "", max_len=120),
        "month": str(row["month"] or ""),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "path": str(row["path"] or ""),
        "url": str(row["url"] or ""),
        "snippet": _clean_snippet(row["snippet"]),
    }


_MAX_FIELD_LEN = 160


def _inline(value: object, *, max_len: int = 0) -> str:
    """Collapse ALL whitespace (newlines/tabs incl.) so a single data value cannot
    open a new Markdown block or inject instructions into the downstream LLM prompt.
    Optionally hard-cap length so one oversized DB field can't bloat the prompt."""
    text = " ".join(("" if value is None else str(value)).split())
    if max_len and len(text) > max_len:
        text = text[:max_len].rstrip() + "…"
    return text


def _like_escape(term: str) -> str:
    """Escape SQL LIKE metacharacters so wildcards in a term (e.g. `AX_OS`) match
    literally. Use with `ESCAPE '\\'` in the query."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _safe_int(value: object) -> int:
    """SQLite is dynamically typed; a declared-INTEGER column in a shared DB can
    still hold text. Coerce defensively instead of crashing."""
    if isinstance(value, int):
        return value
    if isinstance(value, (str, float)):
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0
    return 0


def _clean_snippet(value: object) -> str:
    return _inline(value)


# --------------------------------------------------------------------------- #
# Related company meetings (keyword/label only — no free-text bodies)
# --------------------------------------------------------------------------- #
def _split_env_list(value: str) -> frozenset[str]:
    return frozenset(part.strip() for part in value.split(",") if part.strip())


def load_confirmed_names(glossary_dir: Path | None) -> set[str]:
    """Names known to be real internal people (employee roster + ledger canonical targets)."""
    names: set[str] = set()
    if glossary_dir is None:
        return names
    roster = glossary_dir / "employee_roster.tsv"
    if roster.is_file():
        for line in roster.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip() or line.startswith("name\t"):
                continue
            names.add(line.split("\t", 1)[0].strip())
    ledger = glossary_dir / "identity_ledger.md"
    if ledger.is_file():
        for match in re.finditer(r"→\s*\*\*([^*(]+)", ledger.read_text(encoding="utf-8", errors="replace")):
            token = match.group(1).strip()
            if token:
                names.add(token)
    return {n for n in names if n}


def _months_ago_isodate(months_back: int) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=31 * months_back)
    return cutoff.date().isoformat()


def _distinctive_terms(terms: list[str]) -> list[str]:
    """Drop ubiquitous tool/platform terms so ranking reflects topical relevance."""
    keep: list[str] = []
    for term in terms:
        folded = term.casefold().strip()
        if len(folded) < 2 or folded in GENERIC_MEETING_TERMS:
            continue
        keep.append(term)
    return keep


def search_related_meetings(
    db: Path, terms: list[str], *, limit: int, months_back: int, own_teams: frozenset[str]
) -> list[dict[str, object]]:
    distinctive = _distinctive_terms(terms)
    if not distinctive or limit <= 0:
        return []
    cutoff = _months_ago_isodate(months_back)
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    conn.row_factory = sqlite3.Row
    try:
        # score: title hit (specific, high value) = 3, digest-body hit = 1.
        scores: dict[str, float] = {}
        rows_by_id: dict[str, sqlite3.Row] = {}
        for term in distinctive:
            like = f"%{_like_escape(term)}%"
            try:
                rows = conn.execute(
                    r"""
                    SELECT id, title, meeting_date, meeting_type, owner_team,
                           decision_count, open_action_count, risk_count,
                           digest_json, mentions_json, url, path
                    FROM meeting_notes
                    WHERE meeting_date >= ?
                      AND (title LIKE ? ESCAPE '\' OR digest_json LIKE ? ESCAPE '\')
                    ORDER BY meeting_date DESC
                    LIMIT 25
                    """,
                    (cutoff, like, like),
                ).fetchall()
            except sqlite3.Error:
                return []
            for row in rows:
                key = str(row["id"])
                rows_by_id[key] = row
                title = str(row["title"] or "")
                weight = 3.0 if term.casefold() in title.casefold() else 1.0
                scores[key] = scores.get(key, 0.0) + weight
        # Require at least one title hit OR two distinct term hits to count as "related",
        # so a single generic body match doesn't surface an unrelated meeting.
        ranked_keys = sorted(
            (k for k, s in scores.items() if s >= 2.0),
            key=lambda k: (scores[k], str(rows_by_id[k]["meeting_date"] or "")),
            reverse=True,
        )
        return [_project_meeting(rows_by_id[k], own_teams) for k in ranked_keys[:limit]]
    finally:
        conn.close()


def _project_meeting(row: sqlite3.Row, own_teams: frozenset[str]) -> dict[str, object]:
    digest = _load_json_obj(row["digest_json"])
    mentions = _load_json_list(row["mentions_json"])
    owner_team = str(row["owner_team"] or "")
    is_own = bool(owner_team) and owner_team in own_teams
    projected: dict[str, object] = {
        "title": _inline(row["title"] or "", max_len=200),
        "date": _inline(row["meeting_date"] or "", max_len=40),
        "team": _inline(owner_team, max_len=60),
        "type": _inline(row["meeting_type"] or "", max_len=40),
        "decision_count": _safe_int(row["decision_count"]),
        "open_action_count": _safe_int(row["open_action_count"]),
        "risk_count": _safe_int(row["risk_count"]),
        # Keyword-only fields (safe to inject cross-team).
        "decision_labels": _string_list(digest.get("decision_labels"))[:6],
        "topics": _string_list(digest.get("topics"))[:6],
        "mentions": [m for m in mentions if isinstance(m, str)][:8],
        # Free-text decisions ONLY for explicitly-configured own teams; keyword-only otherwise.
        "decisions": (_string_list(digest.get("decisions"))[:3] if is_own else []),
    }
    return projected


def _load_json_obj(value: object) -> dict[str, object]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _load_json_list(value: object) -> list[object]:
    if not isinstance(value, str) or not value:
        return []
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _string_list(value: object, *, max_len: int = _MAX_FIELD_LEN) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_inline(v, max_len=max_len) for v in value if isinstance(v, str) and v.strip()]


def _render_related_meetings(
    lines: list[str], related: list[dict[str, object]], confirmed_names: set[str]
) -> None:
    lines.extend(
        [
            "",
            "## 관련 회의 (사내 회의록 — 라벨/키워드만)",
            "- 아래는 WDC가 사내 Notion/Slack에서 탐지·요약한 관련 회의의 **라벨·주제·언급인물**이다.",
            "- **회의 본문/결정문은 포함하지 않는다.** 이 회의 transcript에 없는 결정·담당·기한을 여기서 가져오지 말 것.",
            "- 용도는 (1) 반복되는 주제·프로젝트명 표기 일치, (2) 내부 인물 이름 확인, (3) 업무 연속성 인지뿐이다.",
        ]
    )
    if not related:
        lines.append("- 관련 회의 없음")
        return

    all_mentions: OrderedDict[str, None] = OrderedDict()
    for meeting in related:
        title = str(meeting.get("title") or "(제목 없음)")
        date = str(meeting.get("date") or "날짜 미상")
        team = str(meeting.get("team") or "")
        team_str = f" · {team}" if team else ""
        counts = (
            f"결정 {meeting.get('decision_count', 0)} · "
            f"미결액션 {meeting.get('open_action_count', 0)} · "
            f"리스크 {meeting.get('risk_count', 0)}"
        )
        lines.append("")
        lines.append(f"### {date}{team_str} — {title}")
        lines.append(f"- 집계: {counts}")
        labels = _string_list(meeting.get("decision_labels"))
        topics = _string_list(meeting.get("topics"))
        if labels:
            lines.append(f"- 결정 라벨: {', '.join(labels)}")
        if topics:
            lines.append(f"- 주제: {', '.join(topics)}")
        mentions = _string_list(meeting.get("mentions"))
        if mentions:
            for m in mentions:
                all_mentions[m] = None
            lines.append(f"- 언급 인물: {', '.join(mentions)}")
        decisions = _string_list(meeting.get("decisions"))
        if decisions:  # own-team only
            lines.append(f"- (우리 팀) 결정 요지: {'; '.join(decisions)}")

    if all_mentions:
        confirmed = [m for m in all_mentions if m in confirmed_names]
        if confirmed:
            lines.extend(
                [
                    "",
                    "#### 언급 인물 ↔ 사내 명부/사전 대조",
                    f"- 사내 인물로 확인됨: {', '.join(confirmed)}",
                    "- 위 이름이 transcript의 유사 발음 호칭과 매칭되면 정정 후보로 쓰되, 화자 매칭은 별도 검증한다.",
                ]
            )


def render_context(
    db: Path,
    terms: list[str],
    results: dict[str, list[dict[str, str]]],
    *,
    related: list[dict[str, object]] | None = None,
    confirmed_names: set[str] | None = None,
) -> str:
    related = related or []
    confirmed_names = confirmed_names or set()
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

    _render_related_meetings(lines, related, confirmed_names)

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
