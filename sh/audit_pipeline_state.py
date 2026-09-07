#!/usr/bin/env python3
"""Read-only recovery audit for local meeting-note pipeline state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

from notion_publication import (
    readable_path_for,
    readiness_path_for,
    review_dir_for,
    sha256_file,
    validate_readiness_receipt,
    visual_assets_for,
    validate_projection,
)
from validate_meeting_note import validate_note


REVIEW_ARTIFACTS = ("review.md", "review.json", "wiki-update-candidates.md")
READINESS_FIELDS = (
    "source_path",
    "source_sha256",
    "transcript_path",
    "transcript_sha256",
    "readable_path",
    "readable_sha256",
    "review_dir",
)


class CorruptState(ValueError):
    """A state file cannot be safely interpreted as a recovery plan."""


class InvalidInvocation(ValueError):
    """The audit cannot safely write the requested output."""


def opaque_id(key: str) -> str:
    return "note-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def canonical_key(value: str) -> str:
    path = Path(value)
    if (
        path.is_absolute()
        or len(path.parts) != 3
        or path.parts[0] != "notes"
        or path.parts[1] in {"", ".", ".."}
        or path.suffix != ".md"
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise CorruptState("invalid note reference")
    return path.as_posix()


def read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CorruptState(f"invalid {label}") from exc
    if not isinstance(value, dict):
        raise CorruptState(f"invalid {label}")
    return value


def load_pending(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        values = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    except (OSError, UnicodeError) as exc:
        raise CorruptState("invalid pending queue") from exc
    result: set[str] = set()
    for value in values:
        key = canonical_key(value)
        if key in result:
            raise CorruptState("duplicate pending queue reference")
        result.add(key)
    return result


def load_jobs(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = read_json_object(path, label="local pipeline job state")
    files = payload.get("files")
    if not isinstance(files, dict):
        raise CorruptState("invalid local pipeline job state")
    result: dict[str, dict[str, Any]] = {}
    for raw_key, job in files.items():
        if not isinstance(raw_key, str) or not isinstance(job, dict):
            raise CorruptState("invalid local pipeline job state")
        key = canonical_key(raw_key)
        if key in result or job.get("stage") not in {"note", "review"}:
            raise CorruptState("invalid local pipeline job state")
        if "force" in job and not isinstance(job["force"], bool):
            raise CorruptState("invalid local pipeline job state")
        result[key] = job
    return result


def load_published_keys(path: Path) -> tuple[set[str], bool, int]:
    """Read legacy tracking without allowing corrupt state to look empty."""

    if not path.exists():
        return set(), False, 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return set(), True, 0
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, dict):
        return set(), True, 0
    result: set[str] = set()
    unscoped = 0
    for key, value in files.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            return set(), True, 0
        try:
            result.add(canonical_key(key))
        except CorruptState:
            # Old publication runs could accept files outside canonical notes/.
            # Preserve and flag that unsupported scope; valid JSON is not corrupt.
            unscoped += 1
    return result, False, unscoped


def source_path(base: Path, key: str) -> Path:
    candidate = (base / key).resolve()
    notes_root = (base / "notes").resolve()
    try:
        candidate.relative_to(notes_root)
    except ValueError as exc:
        raise CorruptState("invalid note reference") from exc
    return candidate


def readiness_shape_is_valid(payload: Any) -> bool:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return False
    if any(not isinstance(payload.get(field), str) or not payload[field] for field in READINESS_FIELDS):
        return False
    artifacts = payload.get("review_artifacts")
    return (
        isinstance(artifacts, dict)
        and set(artifacts) == set(REVIEW_ARTIFACTS)
        and all(isinstance(value, str) and value for value in artifacts.values())
    )


def note_issue_codes(base: Path, key: str) -> set[str]:
    note = source_path(base, key)
    if not note.is_file():
        return {"NOTE_MISSING"}

    issues: set[str] = set()
    try:
        text = note.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return {"NOTE_UNREADABLE"}
    if validate_note(text):
        issues.add("NOTE_INVALID")

    project = note.parent.name
    transcript = base / "transcripts" / project / f"{note.stem}.txt"
    try:
        transcript_available = transcript.is_file() and bool(transcript.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeError):
        transcript_available = False
    if not transcript_available:
        issues.add("TRANSCRIPT_MISSING")

    try:
        candidate = readable_path_for(note, base)
    except (OSError, ValueError):
        issues.add("PREVIEW_MISSING")
        candidate = None
    if candidate is not None:
        if not candidate.is_file() or not candidate.stat().st_size:
            issues.add("PREVIEW_MISSING")
        else:
            try:
                assets = visual_assets_for(candidate)
                marker = assets[0].get("local_markdown") if assets else None
                projection = validate_projection(
                    text,
                    candidate.read_text(encoding="utf-8"),
                    visual_marker=marker,
                )
                if projection.get("faithful") is not True:
                    issues.add("PREVIEW_STALE")
                for asset in assets:
                    for path_key, hash_key in (
                        ("svg_path", "svg_sha256"),
                        ("embed_html_path", "embed_html_sha256"),
                    ):
                        asset_path = Path(str(asset.get(path_key, "")))
                        if not asset_path.is_file() or asset.get(hash_key) != sha256_file(asset_path):
                            issues.add("PREVIEW_STALE")
            except (OSError, ValueError):
                issues.add("PREVIEW_STALE")

    try:
        review_dir = review_dir_for(note, base)
    except ValueError:
        review_dir = None
        issues.add("REVIEW_MISSING")
    review_complete = False
    if review_dir is not None:
        if any(
            not (review_dir / name).is_file() or not (review_dir / name).stat().st_size
            for name in REVIEW_ARTIFACTS
        ):
            issues.add("REVIEW_MISSING")
        else:
            review_complete = True
            try:
                review_json = json.loads((review_dir / "review.json").read_text(encoding="utf-8"))
                if not isinstance(review_json, dict):
                    issues.add("REVIEW_INVALID")
            except (OSError, UnicodeError, json.JSONDecodeError):
                issues.add("REVIEW_INVALID")

    receipt = readiness_path_for(note, base)
    if not receipt.is_file():
        issues.add("READINESS_MISSING")
        receipt_valid_json = False
    else:
        try:
            receipt_valid_json = readiness_shape_is_valid(
                json.loads(receipt.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeError, json.JSONDecodeError):
            receipt_valid_json = False
        if not receipt_valid_json:
            issues.add("READINESS_INVALID")
    if receipt.is_file() and receipt_valid_json and candidate is not None and review_dir is not None and review_complete and transcript.is_file() and transcript.stat().st_size:
        try:
            validate_readiness_receipt(
                source=note,
                transcript=transcript,
                candidate=candidate,
                review_dir=review_dir,
                base=base,
            )
        except (OSError, RuntimeError) as error:
            del error
            issues.add("READINESS_STALE")
    elif receipt.is_file() and receipt_valid_json:
        issues.add("READINESS_STALE")
    return issues


def invalid_report(code: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "invalid",
        "summary": {
            "note_count": None,
            "pending_count": None,
            "job_count": None,
            "published_tracking_count": None,
            "actionable_count": None,
            "issue_count": 1,
        },
        "global_issue_codes": [code],
        "notes": [],
    }


def audit_state(base: Path) -> tuple[dict[str, Any], int, list[dict[str, Any]]]:
    base = base.expanduser()
    if not base.is_dir():
        return invalid_report("BASE_INVALID"), 2, []
    base = base.resolve()
    try:
        pending = load_pending(base / "state" / "notion-publication" / "pending.txt")
        jobs = load_jobs(base / "state" / "local-pipeline" / "jobs.json")
    except CorruptState:
        return invalid_report("STATE_CORRUPT"), 2, []

    published, publication_corrupt, publication_unscoped = load_published_keys(
        base / "state" / "notion-publication" / "publications.json"
    )
    note_keys = {
        f"notes/{note.parent.name}/{note.name}"
        for note in (base / "notes").glob("*/*.md")
    }
    note_keys.update(pending)
    note_keys.update(jobs)
    records: list[dict[str, Any]] = []
    private_entries: list[dict[str, Any]] = []
    issue_counts: dict[str, int] = {}
    pending_issue_counts: dict[str, int] = {}
    invalid_reference = False

    for key in sorted(note_keys):
        try:
            note = source_path(base, key)
            issues = note_issue_codes(base, key)
            exists = note.is_file()
        except CorruptState:
            note = None
            issues = {"NOTE_REFERENCE_INVALID"}
            exists = False
            invalid_reference = True
        if key in pending and not exists:
            issues.add("QUEUE_ORPHAN_REF")
        if key in jobs:
            issues.add(
                "JOB_STAGED_NOTE" if jobs[key]["stage"] == "note" else "JOB_STAGED_REVIEW"
            )
            if not exists:
                issues.add("JOB_ORPHAN_REF")
        readiness_exists = bool(note and readiness_path_for(note, base).is_file())
        if (
            exists
            and key not in pending
            and key not in jobs
            and not readiness_exists
            and not publication_corrupt
            and key not in published
        ):
            issues.add("LEGACY_NOTE_UNTRACKED")
        for code in issues:
            issue_counts[code] = issue_counts.get(code, 0) + 1
            if key in pending:
                pending_issue_counts[code] = pending_issue_counts.get(code, 0) + 1
        record = {"id": opaque_id(key), "in_pending_queue": key in pending,
                  "has_local_job": key in jobs, "issue_codes": sorted(issues)}
        records.append(record)
        if issues:
            note_target = Path(key)
            transcript = base / "transcripts" / note_target.parts[1] / f"{note_target.stem}.txt"
            missing_note_has_source = False
            if "NOTE_MISSING" in issues:
                try:
                    missing_note_has_source = transcript.is_file() and bool(transcript.read_text(encoding="utf-8").strip())
                except (OSError, UnicodeError):
                    pass
            if "TRANSCRIPT_MISSING" in issues or ("NOTE_MISSING" in issues and not missing_note_has_source):
                action = "restore_source_required"
                proposed_args: list[str] = []
            elif "NOTE_MISSING" in issues:
                action = "generate_missing_note"
                proposed_args = ["--only", f"{note_target.parts[1]}/{note_target.stem}"]
            elif "NOTE_INVALID" in issues:
                action = "regenerate_invalid_note"
                proposed_args = [
                    "--force-notes",
                    "--only",
                    f"{note_target.parts[1]}/{note_target.stem}",
                ]
            elif "READINESS_INVALID" in issues or "NOTE_REFERENCE_INVALID" in issues:
                action = "manual_triage_required"
                proposed_args = []
            elif issues & {
                "PREVIEW_MISSING",
                "PREVIEW_STALE",
                "REVIEW_MISSING",
                "REVIEW_INVALID",
                "READINESS_MISSING",
                "READINESS_STALE",
                "JOB_STAGED_REVIEW",
            }:
                action = "repair_preview_or_review"
                proposed_args = ["--only", f"{note_target.parts[1]}/{note_target.stem}"]
            else:
                action = "manual_triage_required"
                proposed_args = []
            private_entries.append(
                {
                    "id": record["id"],
                    "relative_note_target": key,
                    "action": action,
                    "proposed_run_local_args": proposed_args,
                    "issue_codes": record["issue_codes"],
                }
            )

    actionable = sum(1 for record in records if record["issue_codes"])
    readiness_corrupt = issue_counts.get("READINESS_INVALID", 0) > 0
    global_issues = []
    if publication_corrupt:
        global_issues.append("PUBLICATION_STATE_CORRUPT")
    if readiness_corrupt:
        global_issues.append("READINESS_INVALID")
    if invalid_reference:
        global_issues.append("NOTE_REFERENCE_INVALID")
    invalid = bool(global_issues)
    if publication_unscoped:
        global_issues.append("PUBLICATION_REFERENCE_UNSCOPED")
    needs_action = bool(actionable or publication_unscoped)
    report = {
        "schema_version": 1,
        "status": "invalid" if invalid else ("actionable" if needs_action else "healthy"),
        "summary": {
            "note_count": len(records),
            "pending_count": len(pending),
            "job_count": len(jobs),
            "published_tracking_count": None if publication_corrupt else len(published),
            "published_unscoped_count": None if publication_corrupt else publication_unscoped,
            "actionable_count": actionable,
            "pending_actionable_count": sum(bool(row["issue_codes"]) and row["in_pending_queue"] for row in records),
            "issue_count": sum(issue_counts.values()) + len(global_issues),
        },
        "issue_counts": dict(sorted(issue_counts.items())),
        "pending_issue_counts": dict(sorted(pending_issue_counts.items())),
        "notes": records,
    }
    if global_issues:
        report["global_issue_codes"] = global_issues
    return report, (2 if invalid else (1 if needs_action else 0)), private_entries


def protected_output_dirs(base: Path) -> set[Path]:
    protected = {base.resolve(), (base.parent / "meeting-context-reviewer" / "reviews").resolve()}
    for note in (base / "notes").glob("*/*.md"):
        try:
            protected.add(review_dir_for(note, base).parent.resolve())
        except (OSError, ValueError):
            continue
    return protected


def normalized_new_output(
    path: Path,
    *,
    protected: set[Path],
    allowed_project_output: Path,
) -> Path:
    raw = path.expanduser()
    if os.path.lexists(raw):
        raise InvalidInvocation("output path already exists")
    if not raw.parent.is_dir():
        raise InvalidInvocation("output parent must already exist")
    target = raw.parent.resolve() / raw.name
    try:
        target.relative_to(allowed_project_output)
        return target
    except ValueError:
        pass
    for directory in protected:
        try:
            target.relative_to(directory)
        except ValueError:
            continue
        raise InvalidInvocation("output path is inside a protected input directory")
    return target


def validate_output_paths(base: Path, output: Path | None, private_plan: Path | None) -> tuple[Path | None, Path | None]:
    protected = protected_output_dirs(base)
    allowed_raw = base / "state" / "assessments"
    if os.path.lexists(allowed_raw) and allowed_raw.is_symlink():
        raise InvalidInvocation("assessment output directory must not be a symlink")
    allowed_project_output = allowed_raw.resolve()
    public_target = (
        normalized_new_output(
            output,
            protected=protected,
            allowed_project_output=allowed_project_output,
        )
        if output
        else None
    )
    private_target = (
        normalized_new_output(
            private_plan,
            protected=protected,
            allowed_project_output=allowed_project_output,
        )
        if private_plan
        else None
    )
    if public_target is not None and public_target == private_target:
        raise InvalidInvocation("public output and private plan must use different paths")
    return public_target, private_target


def write_json(path: Path, value: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        help="new public JSON path; existing paths and protected input directories are rejected",
    )
    parser.add_argument(
        "--private-plan",
        type=Path,
        help="new private plan path; existing paths and protected input directories are rejected",
    )
    args = parser.parse_args()

    report, exit_code, private_entries = audit_state(args.base)
    try:
        public_output, private_output = validate_output_paths(
            args.base.expanduser(), args.output, args.private_plan
        )
    except InvalidInvocation:
        print(json.dumps(invalid_report("OUTPUT_PATH_INVALID"), ensure_ascii=False, indent=2))
        return 2
    if public_output:
        write_json(public_output, report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if private_output:
        write_json(
            private_output,
            {
                "schema_version": 1,
                "kind": "local recovery plan only; not executable",
                "entries": private_entries,
            },
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
