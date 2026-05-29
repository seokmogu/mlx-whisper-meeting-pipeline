#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import shutil
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Reject transcripts that contain too little speech content.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    base = Path(os.environ.get("MEETING_BASE_DIR", Path(__file__).resolve().parents[1])).expanduser()
    projects = os.environ.get("MEETING_PROJECTS", "worxphere").split()
    min_chars = int(os.environ.get("MEETING_MIN_TRANSCRIPT_CHARS", "80"))

    rejected = 0
    checked = 0
    for project in projects:
        transcript_dir = base / "transcripts" / project
        if not transcript_dir.exists():
            continue
        for transcript in sorted(transcript_dir.glob("*.txt")):
            if (base / "notes" / project / f"{transcript.stem}.md").exists():
                continue
            checked += 1
            count = _meaningful_char_count(transcript.read_text(encoding="utf-8", errors="ignore"))
            if count >= min_chars:
                continue
            _reject(base, project, transcript, f"meaningful transcript characters {count} below minimum {min_chars}", dry_run=args.dry_run)
            rejected += 1

    print("---")
    if args.dry_run:
        print("dry-run: no transcripts or audio moved")
    print(f"low-content transcripts: checked={checked}, rejected={rejected}")
    return 0


def _meaningful_char_count(text: str) -> int:
    cleaned = re.sub(r"\[[^\]]+\]", " ", text)
    cleaned = re.sub(r"\bSPEAKER[_ -]?\d+\b|Speaker [A-Z]|화자 [A-Z]", " ", cleaned, flags=re.IGNORECASE)
    return len(re.findall(r"[0-9A-Za-z가-힣]", cleaned))


def _reject(base: Path, project: str, transcript: Path, reason: str, *, dry_run: bool) -> None:
    transcript_target = _unique_path(base / "state" / "rejected-transcripts" / project / transcript.name)
    audio = base / "audio" / project / f"{transcript.stem}.m4a"
    audio_target = _unique_path(base / "state" / "rejected-audio" / project / audio.name)
    if dry_run:
        audio_part = f", audio -> {audio_target.relative_to(base)}" if audio.exists() else ""
        print(f"dry-run reject low-content transcript: {project}/{transcript.name} -> {transcript_target.relative_to(base)}{audio_part} ({reason})")
        return

    transcript_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(transcript), transcript_target)
    transcript_target.with_suffix(transcript_target.suffix + ".reason.txt").write_text(reason + "\n", encoding="utf-8")
    if audio.exists():
        audio_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(audio), audio_target)
    print(f"rejected low-content transcript: {project}/{transcript.name} -> {transcript_target.relative_to(base)} ({reason})")


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"cannot find unique path for {path}")


if __name__ == "__main__":
    raise SystemExit(main())
