#!/usr/bin/env python3
"""Upload newly generated Markdown meeting notes to a Notion database.

The normal pipeline queues only files created in the current run. Existing
notes are not backfilled unless explicitly passed with --upload-baseline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


LOCAL_TZ = ZoneInfo(os.getenv("MEETING_NOTES_TIMEZONE", "Asia/Seoul"))
BLOCKS_INITIAL_CREATE = 30


def repo_base() -> Path:
    return Path(__file__).resolve().parents[1]


def project_root() -> Path:
    return repo_base().parent


def add_toolkit_path() -> None:
    toolkit_dir = Path(
        os.getenv("NOTION_NATIVE_TOOLKIT_DIR", str(project_root() / "notion-native-toolkit"))
    ).expanduser()
    src = toolkit_dir / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


add_toolkit_path()

from notion_native_toolkit.markdown import markdown_to_notion_blocks  # noqa: E402
from notion_native_toolkit.toolkit import NotionToolkit  # noqa: E402
from notion_native_toolkit.writer import NotionWriter  # noqa: E402


@dataclass
class UploadResult:
    file: str
    action: str
    title: str = ""
    page_id: str = ""
    url: str = ""
    reason: str = ""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"files": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"files": {}}
    if not isinstance(data.get("files"), dict):
        data["files"] = {}
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def relative_key(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path.resolve())


def read_pending(path: Path | None) -> list[str]:
    if path is None or not path.exists():
        return []
    result = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        value = raw.strip()
        if value and not value.startswith("#"):
            result.append(value)
    return result


def write_pending(path: Path | None, pending: list[str]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    unique = sorted(dict.fromkeys(pending))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(f"{item}\n" for item in unique), encoding="utf-8")
    tmp.replace(path)


def resolve_note_path(value: str, base: Path) -> Path:
    raw = Path(value).expanduser()
    if raw.is_absolute():
        return raw
    candidate = base / value
    if candidate.exists():
        return candidate
    return Path.cwd() / value


def rich_text(content: str) -> list[dict[str, Any]]:
    chunks = []
    for i in range(0, len(content), 2000):
        chunks.append({"type": "text", "text": {"content": content[i : i + 2000]}})
    return chunks or [{"type": "text", "text": {"content": ""}}]


def first_h1(content: str) -> str | None:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            title = stripped[2:].strip()
            if title and title != "미팅노트":
                return title
            return None
    return None


def summary_title(content: str) -> str | None:
    in_summary = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "## 요약":
            in_summary = True
            continue
        if in_summary and stripped.startswith("## "):
            return None
        if not in_summary or not stripped:
            continue
        stripped = re.sub(r"^[-*]\s*", "", stripped)
        sentence = re.split(r"(?<=[.!?。])\s+", stripped)[0].strip()
        if not sentence:
            continue
        sentence = re.sub(r"^(이번\s+)?회의에서는\s*", "", sentence)
        if len(sentence) > 45:
            sentence = sentence[:45].rstrip() + "..."
        return sentence
    return None


def parse_datetime(path: Path, content: str) -> datetime | None:
    header = re.search(r"\[Notion AI 전사 .+? — (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC\]", content)
    if header:
        return datetime.strptime(header.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)

    stem = path.stem
    patterns = [
        (r"^notion_(\d{8})_(\d{6})_", timezone.utc),
        (r"^(\d{8})[ _](\d{6})$", LOCAL_TZ),
        (r"^.*?(\d{8})[ _](\d{6}).*$", LOCAL_TZ),
    ]
    for pattern, tz in patterns:
        match = re.match(pattern, stem)
        if not match:
            continue
        date_part, time_part = match.group(1), match.group(2)
        return datetime.strptime(date_part + time_part, "%Y%m%d%H%M%S").replace(tzinfo=tz)

    voice = re.match(r"^음성_(\d{6})_(\d{6})$", stem)
    if voice:
        date_part, time_part = voice.group(1), voice.group(2)
        return datetime.strptime("20" + date_part + time_part, "%Y%m%d%H%M%S").replace(tzinfo=LOCAL_TZ)

    return None


def title_from_file(path: Path, content: str, dt: datetime | None) -> str:
    h1 = first_h1(content)
    if h1:
        return h1

    stem = path.stem
    notion = re.match(r"^notion_(\d{8})_(\d{6})_(.+)$", stem)
    if notion:
        return notion.group(3).replace("_", " ").strip()

    generated = summary_title(content)
    if generated:
        return generated
    if dt is not None:
        return f"{dt.astimezone(LOCAL_TZ).strftime('%Y-%m-%d %H:%M')} meeting note"
    return stem


def extract_participants(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("- 언급된 인물:") or stripped.startswith("- 인물:"):
            return stripped.split(":", 1)[1].strip()[:2000]
        if stripped.lower().startswith("- participants:"):
            return stripped.split(":", 1)[1].strip()[:2000]
    return ""


def prop_by_type(properties: dict[str, Any], prop_type: str, preferred: list[str]) -> str | None:
    for name in preferred:
        prop = properties.get(name)
        if isinstance(prop, dict) and prop.get("type") == prop_type:
            return name
    for name, prop in properties.items():
        if isinstance(prop, dict) and prop.get("type") == prop_type:
            return name
    return None


def db_schema(client: Any, database_id: str) -> dict[str, str | None]:
    database = client.fetch_database(database_id)
    if database is None:
        raise RuntimeError(f"Failed to fetch Notion database: {database_id}")
    properties = database.get("properties")
    if not isinstance(properties, dict):
        raise RuntimeError("Notion database response has no properties")
    return {
        "title": prop_by_type(properties, "title", ["이름", "Name", "Title", "title"]),
        "date": prop_by_type(properties, "date", ["미팅일시", "Meeting Date", "Date", "날짜"]),
        "participants": prop_by_type(properties, "rich_text", ["참여자", "Participants"]),
        "meeting_type": prop_by_type(properties, "select", ["회의구분", "Type", "구분"]),
    }


def find_existing_page(client: Any, database_id: str, title_prop: str, title: str) -> dict[str, Any] | None:
    payload = {"filter": {"property": title_prop, "title": {"equals": title}}}
    rows = client.query_database(database_id, payload=payload)
    if not rows:
        return None
    return rows[0]


def page_url(page: dict[str, Any]) -> str:
    url = page.get("url")
    return url if isinstance(url, str) else ""


def upload_one(
    path: Path,
    base: Path,
    database_id: str,
    client: Any,
    writer: NotionWriter,
    schema: dict[str, str | None],
    state: dict[str, Any],
    dry_run: bool,
    upload_baseline: bool,
) -> UploadResult:
    if not path.exists():
        return UploadResult(file=str(path), action="missing", reason="file does not exist")
    if path.suffix.lower() != ".md":
        return UploadResult(file=str(path), action="skipped", reason="not a markdown file")

    key = relative_key(path, base)
    content_hash = sha256_file(path)
    existing_state = state["files"].get(key)
    if isinstance(existing_state, dict) and existing_state.get("sha256") == content_hash:
        if existing_state.get("action") == "baseline_existing" and not upload_baseline:
            reason = "pre-existing note intentionally not backfilled"
        elif existing_state.get("action") == "baseline_existing" and upload_baseline:
            reason = ""
        else:
            reason = "already uploaded with same hash"
        if reason:
            return UploadResult(
                file=key,
                action="skipped",
                title=str(existing_state.get("title", "")),
                page_id=str(existing_state.get("page_id", "")),
                url=str(existing_state.get("url", "")),
                reason=reason,
            )

    content = path.read_text(encoding="utf-8")
    dt = parse_datetime(path, content)
    title = title_from_file(path, content, dt)

    title_prop = schema["title"]
    if title_prop is None:
        raise RuntimeError("Notion database has no title property")

    body_content = re.sub(r"^#\s+.+?\s*\n+", "", content, count=1)
    blocks, _pending = markdown_to_notion_blocks(body_content, source_file_path=str(path))

    properties: dict[str, Any] = {title_prop: {"title": rich_text(title)}}
    date_prop = schema["date"]
    if date_prop and dt is not None:
        properties[date_prop] = {"date": {"start": dt.isoformat(timespec="seconds")}}

    participants_prop = schema["participants"]
    participants = extract_participants(content)
    if participants_prop and participants:
        properties[participants_prop] = {"rich_text": rich_text(participants)}

    meeting_type_prop = schema["meeting_type"]
    meeting_type = os.getenv("NOTION_UPLOAD_MEETING_TYPE", "").strip()
    if meeting_type_prop and meeting_type:
        properties[meeting_type_prop] = {"select": {"name": meeting_type}}

    state_page_id = ""
    if isinstance(existing_state, dict):
        maybe_page_id = existing_state.get("page_id")
        if isinstance(maybe_page_id, str):
            state_page_id = maybe_page_id

    if state_page_id:
        existing_page = client.fetch_page(state_page_id)
        if existing_page is not None and not existing_page.get("archived"):
            if dry_run:
                return UploadResult(
                    file=key,
                    action="would_update",
                    title=title,
                    page_id=state_page_id,
                    url=page_url(existing_page),
                    reason=f"{len(blocks)} blocks",
                )
            client.update_page(state_page_id, {"properties": properties})
            writer.replace_page_content(state_page_id, blocks)
            url = page_url(existing_page)
            state["files"][key] = {
                "sha256": content_hash,
                "page_id": state_page_id,
                "url": url,
                "title": title,
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            return UploadResult(file=key, action="updated", title=title, page_id=state_page_id, url=url)

    existing_page = find_existing_page(client, database_id, title_prop, title)
    if existing_page is not None:
        result = UploadResult(
            file=key,
            action="skipped",
            title=title,
            page_id=str(existing_page.get("id", "")),
            url=page_url(existing_page),
            reason="matching title already exists in Notion DB",
        )
        if not dry_run:
            state["files"][key] = {
                "sha256": content_hash,
                "page_id": result.page_id,
                "url": result.url,
                "title": title,
                "action": "matched_existing",
            }
        return result

    if dry_run:
        return UploadResult(file=key, action="would_create", title=title, reason=f"{len(blocks)} blocks")

    initial_blocks = blocks[:BLOCKS_INITIAL_CREATE]
    remaining_blocks = blocks[BLOCKS_INITIAL_CREATE:]
    page = client.create_page(
        {
            "parent": {"database_id": database_id},
            "properties": properties,
            "children": initial_blocks,
        }
    )
    if page is None:
        page = client.create_page({"parent": {"database_id": database_id}, "properties": properties})
        remaining_blocks = blocks
    if page is None:
        raise RuntimeError(f"Failed to create Notion page for {key}")

    page_id = page.get("id")
    if not isinstance(page_id, str) or not page_id:
        raise RuntimeError("Notion did not return a page id")
    if remaining_blocks:
        writer.append_blocks(page_id, remaining_blocks)

    url = page_url(page)
    state["files"][key] = {
        "sha256": content_hash,
        "page_id": page_id,
        "url": url,
        "title": title,
        "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return UploadResult(file=key, action="created", title=title, page_id=page_id, url=url)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*", help="Markdown files to upload")
    ap.add_argument("--pending-file", help="Queue file containing newly generated notes")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--mark-existing",
        action="store_true",
        help="Mark current notes/<project>/*.md files as pre-existing baseline without uploading",
    )
    ap.add_argument(
        "--upload-baseline",
        action="store_true",
        help="Allow explicitly listed baseline_existing files to be uploaded",
    )
    ap.add_argument("--profile", default=os.getenv("NOTION_NATIVE_PROFILE"))
    ap.add_argument("--database-id", default=os.getenv("NOTION_UPLOAD_DATABASE_ID", ""))
    ap.add_argument(
        "--state",
        default=os.getenv(
            "NOTION_UPLOAD_STATE",
            str(repo_base() / "state" / "notion-upload" / "meeting-notes.json"),
        ),
    )
    return ap.parse_args()


def meeting_projects() -> list[str]:
    raw = os.getenv("MEETING_PROJECTS", "projectA projectB").strip()
    return [p for p in raw.split() if p]


def mark_existing_baseline(base: Path, state_path: Path) -> int:
    state = load_state(state_path)
    count = 0
    for project in meeting_projects():
        for path in sorted((base / "notes" / project).glob("*.md")):
            key = relative_key(path, base)
            current = state["files"].get(key)
            if isinstance(current, dict) and current.get("page_id"):
                continue
            content = path.read_text(encoding="utf-8")
            dt = parse_datetime(path, content)
            state["files"][key] = {
                "sha256": sha256_file(path),
                "title": title_from_file(path, content, dt),
                "action": "baseline_existing",
            }
            count += 1
    save_state(state_path, state)
    return count


def main() -> int:
    args = parse_args()
    base = repo_base()
    state_path = Path(args.state).expanduser()

    if args.mark_existing:
        count = mark_existing_baseline(base, state_path)
        print(f"marked pre-existing notes: {count}")
        return 0

    database_id = args.database_id.replace("-", "").strip()
    if not database_id:
        print("NOTION_UPLOAD_DATABASE_ID not set, skip Notion upload")
        return 0

    pending_path = Path(args.pending_file).expanduser() if args.pending_file else None
    requested = sorted(dict.fromkeys(read_pending(pending_path) + args.files))
    if not requested:
        print("notion upload: no pending notes")
        return 0

    toolkit = NotionToolkit.from_profile(args.profile)
    client = toolkit.require_client()
    writer = toolkit.require_writer()
    schema = db_schema(client, database_id)
    state = load_state(state_path)

    remaining_pending: list[str] = []
    results: list[UploadResult] = []
    failures = 0
    for item in requested:
        path = resolve_note_path(item, base)
        try:
            result = upload_one(
                path=path,
                base=base,
                database_id=database_id,
                client=client,
                writer=writer,
                schema=schema,
                state=state,
                dry_run=args.dry_run,
                upload_baseline=args.upload_baseline,
            )
        except Exception as exc:
            result = UploadResult(file=item, action="error", reason=str(exc))
            remaining_pending.append(item)
            failures += 1
        else:
            if result.action == "error":
                remaining_pending.append(item)
                failures += 1
            elif result.action == "missing":
                failures += 1
            elif result.action == "would_create":
                remaining_pending.append(item)
        results.append(result)

    if not args.dry_run:
        save_state(state_path, state)
        write_pending(pending_path, remaining_pending)

    print(json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
