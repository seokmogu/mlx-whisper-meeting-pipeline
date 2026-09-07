#!/usr/bin/env python3
"""Resume owned note/review work without backfilling historical meetings."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from notion_publication import (
    enqueue, readable_path_for, readiness_path_for, render_files, save_json, sha256_file,
)
from validate_meeting_note import validate_note


def normalize_target(value: str) -> str:
    return value.removesuffix(".md").removesuffix(".txt")


def matches(key: str, projects: list[str], only: str) -> bool:
    path = Path(key)
    if len(path.parts) != 3 or path.parts[0] != "notes" or path.suffix != ".md":
        raise ValueError("invalid local pipeline job path")
    if path.parts[1] not in projects:
        return False
    return not only or normalize_target(only) in (path.stem, f"{path.parts[1]}/{path.stem}")


def plan_jobs(base: Path, projects: list[str], only: str, force: bool) -> dict:
    path = base / "state/local-pipeline/jobs.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"files": {}}
    if not isinstance(state, dict) or not isinstance(state.get("files"), dict):
        raise ValueError("invalid local pipeline job state; preserve it for recovery")
    for key, job in state["files"].items():
        matches(key, projects, only)
        if not isinstance(job, dict) or job.get("stage") not in ("note", "review"):
            raise ValueError("invalid local pipeline job stage")
    for project in projects:
        for transcript in sorted((base / "transcripts" / project).glob("*.txt")):
            key = f"notes/{project}/{transcript.stem}.md"
            if not matches(key, projects, only):
                continue
            if force or not (base / key).is_file():
                if not (force or only or key in state["files"] or
                        (base / "audio" / project / f"{transcript.stem}.m4a").is_file()):
                    # Old transcript-only files are not a new watcher event.
                    # Resume them through an existing job or an explicit target.
                    continue
                state["files"][key] = {"stage": "note", "force": force}
            elif only and key not in state["files"]:
                # An explicit existing target is also a bounded repair request.
                state["files"][key] = {"stage": "review", "force": False}
    return state


def require_note(note: Path, transcript: Path) -> None:
    if not transcript.is_file() or not transcript.stat().st_size:
        raise ValueError("nonempty source transcript is required")
    errors = validate_note(note.read_text(encoding="utf-8"))
    if errors:
        raise ValueError("invalid canonical note: " + "; ".join(errors))


def complete_job(base: Path, reviewer: Path, profile: str, key: str, job: dict, checkpoint) -> None:
    note = base / key
    project = Path(key).parts[1]
    transcript = base / "transcripts" / project / f"{note.stem}.txt"
    if not transcript.is_file() or not transcript.stat().st_size:
        raise ValueError("nonempty source transcript is required")
    if job["stage"] == "note":
        args = [str(base / "sh/make-notes.sh"), "--only", f"{project}/{note.stem}"]
        if job.get("force"):
            args.append("--force")
        subprocess.run(args, check=True, env={**os.environ, "MEETING_BASE_DIR": str(base)})
        require_note(note, transcript)
        job["stage"] = "review"
        checkpoint()
    require_note(note, transcript)
    source_hash = sha256_file(note)
    transcript_hash = sha256_file(transcript)
    render_files(base=base, files=[key], validator=base / "sh/validate_meeting_note.py")
    if not reviewer.is_dir():
        raise FileNotFoundError(f"meeting-context-reviewer not found: {reviewer}")
    reviews = reviewer / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"_+", "_", re.sub(r"\s+", "_", note.stem)).strip("_")
    destination = reviews / stem
    # Fresh staging prevents stale files from making a partial reviewer run pass.
    with tempfile.TemporaryDirectory(prefix=".local-review-", dir=reviews) as temporary:
        staging = Path(temporary)
        args = ["uv", "run", "meeting-context-reviewer", "review", "--profile", profile,
                "--meeting", str(note), "--out", str(staging)]
        roster = base / "glossary/employee_roster.tsv"
        if roster.is_file() and roster.stat().st_size:
            args += ["--employee-roster", str(roster)]
        subprocess.run(args, cwd=reviewer, check=True)
        for name in ("review.md", "review.json", "wiki-update-candidates.md"):
            artifact = staging / name
            if not artifact.is_file() or not artifact.stat().st_size:
                raise ValueError(f"reviewer did not produce {name}")
        if not isinstance(json.loads((staging / "review.json").read_text()), dict):
            raise ValueError("review.json must contain a JSON object")
        if sha256_file(note) != source_hash or sha256_file(transcript) != transcript_hash:
            raise ValueError("meeting source changed during review; retry required")
        destination.mkdir(parents=True, exist_ok=True)
        for artifact in staging.iterdir():
            if artifact.is_file():
                artifact.replace(destination / artifact.name)
    candidate = readable_path_for(note, base)
    save_json(readiness_path_for(note, base), {
        "schema_version": 1,
        "source_path": str(note.resolve()),
        "source_sha256": source_hash,
        "transcript_path": str(transcript.resolve()),
        "transcript_sha256": transcript_hash,
        "readable_path": str(candidate.resolve()),
        "readable_sha256": sha256_file(candidate),
        "review_dir": str(destination.resolve()),
        "review_artifacts": {
            name: sha256_file(destination / name)
            for name in ("review.md", "review.json", "wiki-update-candidates.md")
        },
    })
    enqueue(base=base, pending_file=base / "state/notion-publication/pending.txt", files=[key])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--reviewer", type=Path, required=True)
    parser.add_argument("--profile", default="profiles/ax-os")
    parser.add_argument("--only", default="")
    parser.add_argument("--force-notes", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--has-work", action="store_true")
    args = parser.parse_args()
    base = args.base.resolve()
    projects = os.environ.get("MEETING_PROJECTS", "worxphere").split()
    state = plan_jobs(base, projects, args.only, args.force_notes)
    targets = [key for key in state["files"] if matches(key, projects, args.only)]
    if args.has_work:
        print(len(targets))
        return 0
    if args.dry_run:
        for key in targets:
            print(f"dry-run: resume {state['files'][key]['stage']} stage: {key}")
        print(f"dry-run: {len(targets)} note/review job(s); no job state written")
        return 0
    if not targets:
        print("no pending note/review jobs")
        return 0
    state_path = base / "state/local-pipeline/jobs.json"

    def checkpoint():
        save_json(state_path, state)

    checkpoint()
    failed = 0
    for key in targets:
        try:
            complete_job(base, args.reviewer.resolve(), args.profile, key, state["files"][key], checkpoint)
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            failed += 1
            print(f"local completion failed: {key}: {error}", file=sys.stderr)
            continue
        del state["files"][key]
        checkpoint()
        print(f"local completion ready: {key}")
    print(f"local completion: ready={len(targets) - failed}, failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
