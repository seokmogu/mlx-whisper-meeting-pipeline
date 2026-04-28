#!/usr/bin/env python3
"""Fetch Notion AI meeting transcripts via the public Notion API.

Runs on the local host. Queries configured meeting databases,
retrieves each page's markdown with `include_transcript=true`, extracts the
`<transcript>` section, and writes one `.txt` per meeting into the target
directory in the same format as `import_notion_db.py` (so make-notes.sh
treats both sources identically).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
RATE_LIMIT_SLEEP = 0.4  # ~3 req/s

_SLUG_ALLOWED = re.compile(r"[^\w가-힣\s-]", flags=re.UNICODE)
_SLUG_SPACE = re.compile(r"\s+")
_SLUG_TRIM = re.compile(r"[‣‧·•]")
_TRANSCRIPT_RE = re.compile(r"<transcript>(.*?)</transcript>", re.DOTALL | re.IGNORECASE)
_OMITTED_RE = re.compile(r"^\s*Transcript omitted", re.IGNORECASE)


def slugify(title: str) -> str:
    t = _SLUG_TRIM.sub("", title)
    t = _SLUG_ALLOWED.sub("", t)
    t = _SLUG_SPACE.sub("_", t.strip())
    return t[:60] or "untitled"


def notion_request(method: str, path: str, token: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"{NOTION_API}/{path.lstrip('/')}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
        method=method,
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry = int(e.headers.get("Retry-After", "2"))
                time.sleep(retry)
                continue
            body_text = e.read().decode(errors="replace")
            raise RuntimeError(f"{method} {path} -> {e.code}: {body_text[:200]}") from e
        except urllib.error.URLError as e:
            if attempt < 3:
                time.sleep(1 + attempt)
                continue
            raise
    raise RuntimeError(f"{method} {path} failed after retries")


def query_database(db_id: str, token: str) -> list[dict]:
    pages: list[dict] = []
    cursor: str | None = None
    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = notion_request("POST", f"databases/{db_id}/query", token, body)
        pages.extend(r.get("results", []))
        if not r.get("has_more"):
            break
        cursor = r.get("next_cursor")
        time.sleep(RATE_LIMIT_SLEEP)
    return pages


def page_title(page: dict) -> str:
    for _, v in page.get("properties", {}).items():
        if v.get("type") == "title" and v.get("title"):
            return "".join(t.get("plain_text", "") for t in v["title"]).strip()
    return ""


def extract_transcript(markdown: str) -> str:
    m = _TRANSCRIPT_RE.search(markdown)
    if not m:
        return ""
    text = m.group(1).strip()
    if _OMITTED_RE.match(text):
        return ""
    # Collapse tab indentation added by Notion markdown renderer
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def created_dt(page: dict) -> datetime:
    ts = page.get("created_time") or page.get("last_edited_time")
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", help="transcripts output directory")
    ap.add_argument("--db", action="append", required=True, help="database ID to query (repeatable)")
    ap.add_argument("--since", help="only include pages created on/after YYYY-MM-DD")
    ap.add_argument("--min-chars", type=int, default=50, help="skip transcripts shorter than N chars")
    ap.add_argument("--token-env", default="NOTION_TOKEN", help="env var holding the Notion token")
    args = ap.parse_args()

    token = os.environ.get(args.token_env, "").strip()
    if not token:
        print(f"error: ${args.token_env} not set", file=sys.stderr)
        sys.exit(2)

    since = None
    if args.since:
        since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    skipped_short = 0
    skipped_exists = 0
    skipped_no_transcript = 0
    errors = 0

    for db_id in args.db:
        try:
            pages = query_database(db_id, token)
        except Exception as e:
            print(f"db {db_id[:8]} query failed: {e}", file=sys.stderr)
            errors += 1
            continue

        for page in pages:
            dt = created_dt(page)
            if since and dt < since:
                continue
            title = page_title(page) or "(제목 없음)"
            slug = slugify(title) if title != "(제목 없음)" else "notion"
            date_str = dt.strftime("%Y%m%d_%H%M%S")
            pid_suffix = page["id"].replace("-", "")[:8]
            out_path = out_dir / f"notion_{date_str}_{slug}_{pid_suffix}.txt"
            if out_path.exists():
                skipped_exists += 1
                continue

            page_id = page["id"]
            try:
                r = notion_request(
                    "GET",
                    f"pages/{page_id}/markdown?include_transcript=true",
                    token,
                )
            except Exception as e:
                print(f"page {page_id[:8]} markdown failed: {e}", file=sys.stderr)
                errors += 1
                continue

            markdown = r.get("markdown", "") or ""
            transcript = extract_transcript(markdown)
            if not transcript:
                skipped_no_transcript += 1
                time.sleep(RATE_LIMIT_SLEEP)
                continue
            if len(transcript) < args.min_chars:
                skipped_short += 1
                time.sleep(RATE_LIMIT_SLEEP)
                continue

            header = (
                f"[Notion AI 전사 — {title} — "
                f"{dt.strftime('%Y-%m-%d %H:%M UTC')}]\n"
                "화자 구분 없음. 발화 단위로 줄바꿈.\n\n"
            )
            out_path.write_text(header + transcript + "\n", encoding="utf-8")
            written += 1
            time.sleep(RATE_LIMIT_SLEEP)

    print(
        f"written: {written}, skipped_short: {skipped_short}, "
        f"skipped_exists: {skipped_exists}, skipped_no_transcript: {skipped_no_transcript}, "
        f"errors: {errors}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
