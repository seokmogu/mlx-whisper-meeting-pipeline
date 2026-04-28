#!/usr/bin/env python3
"""Relabel an existing timestamped transcript with a diarization JSON file."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


LINE_RE = re.compile(
    r"^\[(?P<start>\d\d:\d\d:\d\d) - (?P<end>\d\d:\d\d:\d\d)\] (?P<speaker>[A-Z?]+): (?P<text>.*)$"
)


def parse_time(value: str) -> float:
    h, m, s = [int(part) for part in value.split(":")]
    return h * 3600 + m * 60 + s


def fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def best_speaker(start: float, end: float, segments: list[dict]) -> str | None:
    mid = (start + end) / 2.0
    best = None
    best_overlap = 0.0
    for seg in segments:
        seg_start = float(seg["start"])
        seg_end = float(seg["end"])
        if seg_start <= mid <= seg_end:
            return str(seg["speaker"])
        overlap = max(0.0, min(end, seg_end) - max(start, seg_start))
        if overlap > best_overlap:
            best_overlap = overlap
            best = str(seg["speaker"])
    return best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript")
    ap.add_argument("diarization_json")
    ap.add_argument("output")
    args = ap.parse_args()

    diarization = json.loads(Path(args.diarization_json).read_text(encoding="utf-8"))
    segments = diarization.get("segments", [])
    speaker_map: dict[str, str] = {}
    next_label = iter("ABCDEFGHIJ")

    def label(raw: str | None) -> str:
        if not raw:
            return "?"
        if raw not in speaker_map:
            speaker_map[raw] = next(next_label)
        return speaker_map[raw]

    out_lines = []
    for line in Path(args.transcript).read_text(encoding="utf-8").splitlines():
        match = LINE_RE.match(line)
        if not match:
            out_lines.append(line)
            continue
        start = parse_time(match.group("start"))
        end = parse_time(match.group("end"))
        raw_speaker = best_speaker(start, end, segments)
        out_lines.append(
            f"[{fmt_time(start)} - {fmt_time(end)}] {label(raw_speaker)}: {match.group('text')}"
        )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(f"speaker_map: {speaker_map}")


if __name__ == "__main__":
    main()
