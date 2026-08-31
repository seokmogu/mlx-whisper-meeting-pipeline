#!/usr/bin/env python3
"""Audit rebuilt meeting-note, readable, asset, attendee, and review completeness."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


NOTION = load_module("candidate_notion_publication", SCRIPT_DIR / "notion_publication.py")
NOTE_VALIDATOR = load_module("candidate_note_validator", SCRIPT_DIR / "validate_meeting_note.py")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_confirmed_attendees(source_base: Path, project: str, stem: str) -> list[str]:
    path = source_base / "state" / "meeting-attendees" / project / f"{stem}.txt"
    if not path.is_file():
        return []
    roster = NOTION.load_roster_names(source_base)
    return NOTION.parse_attendee_names(path.read_text(encoding="utf-8"), roster)


def first_screen_lines(readable_text: str) -> int:
    end = readable_text.find("## 4. 상세 논의와 근거")
    return len(readable_text[: end if end >= 0 else len(readable_text)].splitlines())


def audit_one(batch_root: Path, source_base: Path, note: Path) -> dict[str, Any]:
    project = note.parent.name
    stem = note.stem
    text = note.read_text(encoding="utf-8")
    errors = NOTE_VALIDATOR.validate_note(text)
    warnings: list[str] = []

    source_match = re.search(r"(?m)^-\s*원본:\s*`?(/[^`\s()]+\.txt)", text)
    transcript = Path(source_match.group(1).strip()).expanduser() if source_match else None
    if transcript is None or not transcript.is_file():
        errors.append("source transcript path is missing or unreadable")

    confirmed = parse_confirmed_attendees(source_base, project, stem)
    attendees_body = NOTE_VALIDATOR._subsection_body(text, "### 5.1 참석자/언급 인물")
    missing_attendees = [name for name in confirmed if name not in attendees_body]
    if missing_attendees:
        errors.append("confirmed attendees missing from 5.1: " + ", ".join(missing_attendees))

    primary = "\n".join(
        NOTE_VALIDATOR._section_body(text, heading)
        for heading in NOTE_VALIDATOR.REQUIRED_HEADINGS[:3]
    )
    required_refs = set(re.findall(r"§(4\.\d+)", primary))
    evidence_refs = set(re.findall(r"(?m)^###\s+(4\.\d+)\s+", text))
    missing_refs = sorted(required_refs - evidence_refs)
    if missing_refs:
        errors.append("reader-layer evidence references are missing: " + ", ".join(missing_refs))

    readable = NOTION.readable_path_for(note, batch_root)
    if not readable.is_file():
        errors.append(f"readable projection missing: {readable}")
        readable_text = ""
        assets: list[dict[str, Any]] = []
    else:
        readable_text = readable.read_text(encoding="utf-8")
        assets = NOTION.visual_assets_for(readable)
        marker = assets[0].get("local_markdown") if assets else None
        projection = NOTION.validate_projection(text, readable_text, visual_marker=marker)
        if projection.get("faithful") is not True:
            errors.append("readable projection is not deterministic for the current note")

        anchors = set(re.findall(r"\[[^\]]+\]\((#[^)]+)\)", readable_text))
        heading_anchors = {
            NOTION._heading_anchor(match.group(1), match.group(2).strip())
            for match in re.finditer(r"(?m)^###\s+(4\.\d+)\s+(.+)$", readable_text)
        }
        broken = sorted(anchors - heading_anchors)
        if broken:
            errors.append("readable has broken local evidence anchors: " + ", ".join(broken))

        top_lines = first_screen_lines(readable_text)
        if top_lines > 65:
            warnings.append(f"readable first-screen path is dense: {top_lines} lines before section 4")

    for asset in assets:
        for path_key, hash_key in (
            ("svg_path", "svg_sha256"),
            ("embed_html_path", "embed_html_sha256"),
        ):
            asset_path = Path(str(asset.get(path_key, "")))
            if not asset_path.is_file():
                errors.append(f"visual asset missing: {asset_path}")
            elif asset.get(hash_key) != sha256_file(asset_path):
                errors.append(f"visual asset hash mismatch: {asset_path}")

    review = batch_root / "reviews" / project / stem / "review.md"
    if not review.is_file() or review.stat().st_size == 0:
        errors.append(f"context review missing: {review}")

    action_titles = re.findall(r"(?m)^- \[[ xX]\] \*\*A\d+ · (.+?)\*\*$", text)
    long_actions = [title for title in action_titles if len(title) > 90]
    if long_actions:
        warnings.append(f"long action titles: {len(long_actions)}")

    return {
        "meeting": f"{project}/{stem}",
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "warnings": warnings,
        "confirmed_attendees": confirmed,
        "required_evidence_refs": sorted(required_refs),
        "visualization": assets[0].get("kind", "none") if assets else "none",
        "note_path": str(note.resolve()),
        "readable_path": str(readable.resolve()),
        "review_path": str(review.resolve()),
    }


def render_markdown(results: list[dict[str, Any]]) -> str:
    passed = sum(result["status"] == "pass" for result in results)
    lines = [
        "# Meeting candidate quality audit",
        "",
        f"- 대상: {len(results)}",
        f"- 통과: {passed}",
        f"- 실패: {len(results) - passed}",
        "",
    ]
    for result in results:
        lines.extend([f"## {result['meeting']} · {result['status']}", ""])
        if result["errors"]:
            lines.append("### 오류")
            lines.extend(f"- {value}" for value in result["errors"])
            lines.append("")
        if result["warnings"]:
            lines.append("### 경고")
            lines.extend(f"- {value}" for value in result["warnings"])
            lines.append("")
        lines.extend(
            [
                f"- 시각화: {result['visualization']}",
                f"- 회의록: {result['note_path']}",
                f"- 리더블: {result['readable_path']}",
                f"- 리뷰: {result['review_path']}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-root", type=Path, required=True)
    parser.add_argument("--source-base", type=Path, default=SCRIPT_DIR.parent)
    parser.add_argument("--only")
    args = parser.parse_args()

    batch_root = args.batch_root.resolve()
    source_base = args.source_base.resolve()
    notes = sorted((batch_root / "notes").glob("*/*.md"))
    if args.only:
        wanted = args.only.removesuffix(".md")
        notes = [
            note
            for note in notes
            if note.stem == wanted or f"{note.parent.name}/{note.stem}" == wanted
        ]
    if not notes:
        print("no candidate notes found", file=sys.stderr)
        return 2

    results = [audit_one(batch_root, source_base, note) for note in notes]
    quality_dir = batch_root / "quality"
    quality_dir.mkdir(parents=True, exist_ok=True)
    (quality_dir / "audit.json").write_text(
        json.dumps({"results": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (quality_dir / "audit.md").write_text(render_markdown(results), encoding="utf-8")
    print(json.dumps({"total": len(results), "pass": sum(r["status"] == "pass" for r in results)}, ensure_ascii=False))
    return 0 if all(result["status"] == "pass" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
