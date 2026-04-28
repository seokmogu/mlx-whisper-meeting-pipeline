#!/usr/bin/env python3
"""Extract Notion AI meeting transcripts from the Notion desktop app's local SQLite cache.

Reads `transcription` blocks and their referenced transcript children, writing one
`.txt` per meeting into the target directory. Format matches what make-notes.sh expects
for the "Notion AI" (non-diarized) branch.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def _rich_text_to_plain(rich) -> str:
    if not isinstance(rich, list):
        return ""
    parts = []
    for seg in rich:
        if isinstance(seg, list) and seg and isinstance(seg[0], str):
            parts.append(seg[0])
    return "".join(parts)


def fetch_props_title(conn: sqlite3.Connection, block_id: str) -> str:
    cur = conn.execute("SELECT properties FROM block WHERE id=?", (block_id,))
    row = cur.fetchone()
    if not row or not row[0]:
        return ""
    try:
        props = json.loads(row[0])
    except Exception:
        return ""
    return _rich_text_to_plain(props.get("title", [])).strip()


def fetch_content(conn: sqlite3.Connection, block_id: str) -> list:
    cur = conn.execute("SELECT content FROM block WHERE id=?", (block_id,))
    row = cur.fetchone()
    if not row or not row[0]:
        return []
    try:
        return json.loads(row[0]) or []
    except Exception:
        return []


def fetch_parent_title(conn: sqlite3.Connection, block_id: str, max_depth: int = 6) -> str:
    current = block_id
    for _ in range(max_depth):
        cur = conn.execute(
            "SELECT properties, parent_id, parent_table FROM block WHERE id=?",
            (current,),
        )
        row = cur.fetchone()
        if not row:
            return ""
        props_raw, parent_id, parent_table = row
        if props_raw:
            try:
                props = json.loads(props_raw)
                title = _rich_text_to_plain(props.get("title", [])).strip()
                if title:
                    return title
            except Exception:
                pass
        if parent_table != "block" or not parent_id:
            return ""
        current = parent_id
    return ""


_SLUG_ALLOWED = re.compile(r"[^\w가-힣\s-]", flags=re.UNICODE)
_SLUG_SPACE = re.compile(r"\s+")
_SLUG_TRIM = re.compile(r"[‣‧·•]")


def slugify(title: str) -> str:
    t = _SLUG_TRIM.sub("", title)
    t = _SLUG_ALLOWED.sub("", t)
    t = _SLUG_SPACE.sub("_", t.strip())
    return t[:60] or "untitled"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("db", help="path to notion.db snapshot")
    ap.add_argument("out_dir", help="transcripts output directory")
    ap.add_argument("--since", help="only include transcriptions created on/after YYYY-MM-DD (UTC)")
    ap.add_argument("--min-utterances", type=int, default=3,
                    help="skip transcriptions with fewer than N utterances (default 3)")
    args = ap.parse_args()

    since_ms = None
    if args.since:
        dt = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        since_ms = int(dt.timestamp() * 1000)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)

    q = (
        "SELECT id, created_time, format FROM block "
        "WHERE type='transcription' AND alive=1"
    )
    if since_ms is not None:
        q += f" AND created_time >= {since_ms}"
    q += " ORDER BY created_time"

    written = 0
    skipped_short = 0
    skipped_exists = 0
    skipped_no_transcript = 0

    for bid, created_ms, fmt_raw in conn.execute(q):
        if not fmt_raw:
            skipped_no_transcript += 1
            continue
        try:
            fmt = json.loads(fmt_raw)
        except Exception:
            skipped_no_transcript += 1
            continue
        transcript_id = fmt.get("transcription_transcript_id")
        if not transcript_id:
            skipped_no_transcript += 1
            continue

        utterance_ids = fetch_content(conn, transcript_id)
        lines = []
        for uid in utterance_ids:
            text = fetch_props_title(conn, uid)
            if text:
                lines.append(text)
        if len(lines) < args.min_utterances:
            skipped_short += 1
            continue

        dt = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
        date_str = dt.strftime("%Y%m%d_%H%M%S")
        page_title = fetch_parent_title(conn, bid)
        slug = slugify(page_title) if page_title else "notion"
        name = f"notion_{date_str}_{slug}"
        out_path = out_dir / f"{name}.txt"

        if out_path.exists():
            skipped_exists += 1
            continue

        header = (
            f"[Notion AI 전사 — {page_title or '(제목 없음)'} — "
            f"{dt.strftime('%Y-%m-%d %H:%M UTC')}]\n"
            "화자 구분 없음. 발화 단위로 줄바꿈.\n\n"
        )
        out_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
        written += 1

    print(
        f"written: {written}, skipped_short: {skipped_short}, "
        f"skipped_exists: {skipped_exists}, skipped_no_transcript: {skipped_no_transcript}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
