#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


@dataclass(frozen=True)
class AudioItem:
    path: Path
    project: str
    started_at: datetime | None
    duration: float | None


@dataclass(frozen=True)
class SpeechWindow:
    start: float
    end: float


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare local audio queue before transcription.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    base = Path(os.environ.get("MEETING_BASE_DIR", Path(__file__).resolve().parents[1])).expanduser()
    projects = os.environ.get("MEETING_PROJECTS", "worxphere").split()
    merge_gap = int(os.environ.get("AUDIO_PREP_MERGE_GAP_SECONDS", "180"))
    min_duration = float(os.environ.get("AUDIO_PREP_MIN_DURATION_SECONDS", "10"))
    silence_ratio = float(os.environ.get("AUDIO_PREP_REJECT_SILENCE_RATIO", "0.98"))
    silence_threshold = os.environ.get("AUDIO_PREP_SILENCE_THRESHOLD", "-35dB")
    trim_enabled = _truthy(os.environ.get("AUDIO_PREP_TRIM_OUTER_SILENCE", "1"))
    trim_threshold = os.environ.get("AUDIO_PREP_TRIM_THRESHOLD", silence_threshold)
    trim_silence_duration = float(os.environ.get("AUDIO_PREP_TRIM_SILENCE_DURATION", "0.5"))
    trim_padding = float(os.environ.get("AUDIO_PREP_TRIM_PADDING_SECONDS", "0.4"))
    min_trim = float(os.environ.get("AUDIO_PREP_MIN_TRIM_SECONDS", "1.0"))

    merged = 0
    rejected = 0
    trimmed = 0

    for project in projects:
        audio_dir = base / "audio" / project
        if not audio_dir.exists():
            continue

        pending_transactions = _merge_transaction_manifests(base, project)
        if pending_transactions:
            if args.dry_run:
                print(
                    f"dry-run: {len(pending_transactions)} pending merge transaction(s) for {project} "
                    "would recover before queue preparation; skipping this project's queue"
                )
                continue
            _recover_merge_transactions(base, project, pending_transactions)

        candidates = [
            item
            for item in (_audio_item(base, project, path) for path in sorted(audio_dir.glob("*.m4a")))
            if item and not _already_processed(base, project, item.path.stem)
        ]

        kept: list[AudioItem] = []
        for item in candidates:
            prepared = item
            if trim_enabled:
                prepared, did_trim = _trim_outer_silence(
                    base,
                    item,
                    trim_threshold,
                    trim_silence_duration,
                    trim_padding,
                    min_trim,
                    dry_run=args.dry_run,
                )
                if did_trim:
                    trimmed += 1
            reason = _reject_reason(prepared, silence_threshold, min_duration, silence_ratio)
            if reason:
                _reject_audio(base, prepared, reason, dry_run=args.dry_run)
                rejected += 1
            else:
                kept.append(prepared)

        for group in _adjacent_groups(kept, merge_gap):
            if len(group) < 2:
                continue
            _merge_group(base, group, dry_run=args.dry_run)
            merged += 1

    print("---")
    if args.dry_run:
        print("dry-run: no audio prepared, moved, rejected, trimmed, or merged")
    print(f"audio prepared: merged_groups={merged}, trimmed={trimmed}, rejected={rejected}")
    return 0


def _audio_item(base: Path, project: str, path: Path) -> AudioItem | None:
    started_at = _parse_started_at(path.stem)
    return AudioItem(path=path, project=project, started_at=started_at, duration=_duration_seconds(path))


def _already_processed(base: Path, project: str, stem: str) -> bool:
    return (base / "notes" / project / f"{stem}.md").exists() or (base / "transcripts" / project / f"{stem}.txt").exists()


def _parse_started_at(stem: str) -> datetime | None:
    match = re.match(r"^(\d{8})[ _](\d{6})", stem)
    if not match:
        return None
    try:
        return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _duration_seconds(path: Path) -> float | None:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def _reject_reason(item: AudioItem, silence_threshold: str, min_duration: float, silence_ratio: float) -> str | None:
    if item.duration is None:
        # ffprobe could not read a duration — the signature of a corrupt file or a
        # not-yet-synced iCloud placeholder. Quarantine it; otherwise it stays in the
        # queue forever and aborts every transcribe.sh run (ffmpeg fails under set -e).
        return "duration unavailable (ffprobe failed — corrupt or unreadable audio)"
    if item.duration < min_duration:
        return f"duration {item.duration:.1f}s is below minimum {min_duration:.1f}s"
    silent = _silence_seconds(item.path, silence_threshold)
    if silent is not None and item.duration > 0 and silent / item.duration >= silence_ratio:
        return f"silence ratio {silent / item.duration:.2%} exceeds {silence_ratio:.2%}"
    return None


def _silence_seconds(path: Path, threshold: str) -> float | None:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            f"silencedetect=noise={threshold}:d=0.5",
            "-f",
            "null",
            "-",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return sum(float(value) for value in re.findall(r"silence_duration: ([0-9.]+)", result.stderr))


def _trim_outer_silence(
    base: Path,
    item: AudioItem,
    threshold: str,
    silence_duration: float,
    padding: float,
    min_trim: float,
    *,
    dry_run: bool,
) -> tuple[AudioItem, bool]:
    if item.duration is None:
        return item, False
    window = _outer_speech_window(item.path, item.duration, threshold, silence_duration, padding)
    if not window:
        return item, False
    trimmed_seconds = window.start + max(0.0, item.duration - window.end)
    if trimmed_seconds < min_trim:
        return item, False

    target = _unique_path(base / "state" / "audio-originals" / item.project / item.path.name)
    if dry_run:
        print(
            "dry-run trim outer non-speech: "
            f"{item.project}/{item.path.name} "
            f"keep {window.start:.2f}s..{window.end:.2f}s "
            f"({trimmed_seconds:.2f}s trimmed) -> original backup {target.relative_to(base)}"
        )
        adjusted_start = item.started_at + timedelta(seconds=window.start) if item.started_at else item.started_at
        return (
            AudioItem(
                path=item.path,
                project=item.project,
                started_at=adjusted_start,
                duration=window.end - window.start,
            ),
            True,
        )

    staging_dir = target.parent / ".trim-staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    temp = staging_dir / f".{target.name}.tmp.m4a"
    temp.unlink(missing_ok=True)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{window.start:.3f}",
                "-to",
                f"{window.end:.3f}",
                "-i",
                str(item.path),
                "-vn",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                str(temp),
            ],
            check=True,
        )
        # Keep the original at its active path until the replacement is ready
        # to publish. A failed publish therefore leaves a retryable input
        # rather than an invisible meeting under state/.
        shutil.copy2(item.path, target)
        os.replace(temp, item.path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    print(
        "trimmed outer non-speech: "
        f"{item.project}/{item.path.name} keep {window.start:.2f}s..{window.end:.2f}s "
        f"({trimmed_seconds:.2f}s trimmed); original -> {target.relative_to(base)}"
    )
    adjusted_start = item.started_at + timedelta(seconds=window.start) if item.started_at else item.started_at
    return (
        AudioItem(
            path=item.path,
            project=item.project,
            started_at=adjusted_start,
            duration=_duration_seconds(item.path) or (window.end - window.start),
        ),
        True,
    )


def _outer_speech_window(
    path: Path,
    duration: float,
    threshold: str,
    silence_duration: float,
    padding: float,
) -> SpeechWindow | None:
    events = _silence_events(path, threshold, silence_duration)
    if not events:
        return None

    start = 0.0
    end = duration

    first_start, first_end = events[0]
    if first_start <= 0.05 and first_end is not None:
        start = first_end

    last_start, last_end = events[-1]
    if (last_end is None or last_end >= duration - 0.05) and last_start < duration:
        end = last_start

    start = max(0.0, start - padding)
    end = min(duration, end + padding)
    if end <= start:
        return None
    if start <= 0.0 and end >= duration:
        return None
    return SpeechWindow(start=start, end=end)


def _silence_events(path: Path, threshold: str, silence_duration: float) -> list[tuple[float, float | None]]:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            f"silencedetect=noise={threshold}:d={silence_duration}",
            "-f",
            "null",
            "-",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return []

    events: list[tuple[float, float | None]] = []
    for line in result.stderr.splitlines():
        start_match = re.search(r"silence_start: ([0-9.]+)", line)
        if start_match:
            events.append((float(start_match.group(1)), None))
            continue
        end_match = re.search(r"silence_end: ([0-9.]+)", line)
        if end_match and events and events[-1][1] is None:
            events[-1] = (events[-1][0], float(end_match.group(1)))
    return events


def _reject_audio(base: Path, item: AudioItem, reason: str, *, dry_run: bool) -> None:
    target = _unique_path(base / "state" / "rejected-audio" / item.project / item.path.name)
    if dry_run:
        print(f"dry-run reject audio: {item.project}/{item.path.name} -> {target.relative_to(base)} ({reason})")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(item.path), target)
    target.with_suffix(target.suffix + ".reason.txt").write_text(reason + "\n", encoding="utf-8")
    print(f"rejected audio: {item.project}/{item.path.name} -> {target.relative_to(base)} ({reason})")


def _adjacent_groups(items: list[AudioItem], merge_gap: int) -> list[list[AudioItem]]:
    if merge_gap <= 0:
        return [[item] for item in items]
    dated = [item for item in items if item.started_at and item.duration]
    groups: list[list[AudioItem]] = []
    current: list[AudioItem] = []
    previous_end: datetime | None = None
    for item in sorted(dated, key=lambda candidate: candidate.started_at or datetime.min):
        if not current or previous_end is None:
            current = [item]
        else:
            gap = (item.started_at - previous_end).total_seconds() if item.started_at else merge_gap + 1
            if 0 <= gap <= merge_gap:
                current.append(item)
            else:
                groups.append(current)
                current = [item]
        previous_end = (item.started_at or datetime.min) + timedelta(seconds=item.duration or 0)
    if current:
        groups.append(current)
    return groups


def _merge_group(base: Path, group: list[AudioItem], *, dry_run: bool) -> None:
    project = group[0].project
    first = group[0]
    target_stem = first.started_at.strftime("%Y%m%d_%H%M%S") if first.started_at else _filesystem_stem(first.path.stem)
    target_name = f"{target_stem}_merged.m4a"
    target = _unique_path(base / "audio" / project / target_name)
    names = ", ".join(item.path.name for item in group)
    if dry_run:
        print(f"dry-run merge adjacent audio: {project}/[{names}] -> {target.relative_to(base)}")
        return

    backup_dir = base / "state" / "audio-segments" / project / target.stem
    backup_dir.mkdir(parents=True, exist_ok=True)
    stage = backup_dir / ".merged.stage.m4a"
    stage.unlink(missing_ok=True)
    entries = [
        {
            "source_name": item.path.name,
            "backup_name": _unique_path(backup_dir / item.path.name).name,
        }
        for item in group
    ]
    manifest = backup_dir / "merge-transaction.json"
    _write_merge_manifest(
        manifest,
        {
            "version": 1,
            "phase": "building",
            "target_name": target.name,
            "stage_name": stage.name,
            "sources": entries,
        },
    )

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as list_file:
        list_path = Path(list_file.name)
        for item in group:
            list_file.write(f"file '{_ffmpeg_concat_escape(item.path)}'\n")
    try:
        copy_result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(stage)],
            check=False,
        )
        if copy_result.returncode != 0:
            stage.unlink(missing_ok=True)
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(list_path),
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    str(stage),
                ],
                check=True,
            )
    finally:
        list_path.unlink(missing_ok=True)

    _write_merge_manifest(
        manifest,
        {
            "version": 1,
            "phase": "staged",
            "target_name": target.name,
            "stage_name": stage.name,
            "sources": entries,
        },
    )
    _finish_merge_transaction(base, project, backup_dir, manifest)
    print(f"merged adjacent audio: {project}/[{names}] -> {target.relative_to(base)}; originals -> {backup_dir.relative_to(base)}")


def _merge_transaction_manifests(base: Path, project: str) -> list[Path]:
    root = base / "state" / "audio-segments" / project
    if not root.exists():
        return []
    return sorted(root.glob("*/merge-transaction.json"))


def _recover_merge_transactions(base: Path, project: str, manifests: list[Path] | None = None) -> None:
    for manifest in manifests if manifests is not None else _merge_transaction_manifests(base, project):
        backup_dir = manifest.parent
        payload = _read_merge_manifest(manifest)
        phase = payload["phase"]
        stage = backup_dir / payload["stage_name"]
        if phase == "building":
            # No source has moved in this phase. Drop an incomplete derivative
            # and let the next normal queue pass build it again from originals.
            stage.unlink(missing_ok=True)
            manifest.unlink()
            continue
        _finish_merge_transaction(base, project, backup_dir, manifest, payload)


def _finish_merge_transaction(
    base: Path,
    project: str,
    backup_dir: Path,
    manifest: Path,
    payload: dict | None = None,
) -> None:
    payload = payload or _read_merge_manifest(manifest)
    if payload["phase"] != "staged":
        raise RuntimeError(f"unsupported merge transaction phase: {payload['phase']}")
    target = base / "audio" / project / payload["target_name"]
    stage = backup_dir / payload["stage_name"]
    if not target.exists() and not stage.exists():
        raise RuntimeError(f"merge transaction lost staged output: {manifest}")
    for entry in payload["sources"]:
        source = base / "audio" / project / entry["source_name"]
        backup = backup_dir / entry["backup_name"]
        if backup.exists():
            continue
        if not source.exists():
            raise RuntimeError(f"merge transaction lost source: {source}")
        shutil.move(str(source), backup)
    if not target.exists():
        os.replace(stage, target)
    manifest.unlink()


def _read_merge_manifest(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid merge transaction: {path}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or payload.get("phase") not in {"building", "staged"}
        or not _safe_filename(payload.get("target_name"))
        or not _safe_filename(payload.get("stage_name"))
        or not isinstance(payload.get("sources"), list)
        or not payload["sources"]
    ):
        raise RuntimeError(f"invalid merge transaction: {path}")
    for entry in payload["sources"]:
        if (
            not isinstance(entry, dict)
            or not _safe_filename(entry.get("source_name"))
            or not _safe_filename(entry.get("backup_name"))
        ):
            raise RuntimeError(f"invalid merge transaction: {path}")
    return payload


def _safe_filename(value: object) -> bool:
    return isinstance(value, str) and bool(value) and Path(value).name == value


def _write_merge_manifest(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


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


def _filesystem_stem(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"\s+", "_", value)).strip("_")


def _ffmpeg_concat_escape(path: Path) -> str:
    return str(path).replace("'", "'\\''")


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    raise SystemExit(main())
