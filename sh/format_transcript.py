#!/usr/bin/env python3
"""Format WhisperX JSON output into readable timestamped speaker transcript."""
import json
import sys
from pathlib import Path


def fmt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def main():
    if len(sys.argv) != 3:
        print("usage: format_transcript.py <input.json> <output.txt>", file=sys.stderr)
        sys.exit(1)

    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    data = json.loads(src.read_text())
    segments = data.get("segments", [])

    speaker_map: dict[str, str] = {}
    next_letter = iter("ABCDEFGHIJ")

    def label(speaker_id: str | None) -> str:
        if speaker_id is None:
            return "?"
        if speaker_id not in speaker_map:
            speaker_map[speaker_id] = next(next_letter)
        return speaker_map[speaker_id]

    lines: list[str] = []
    for seg in segments:
        start = fmt_time(seg.get("start", 0.0))
        end = fmt_time(seg.get("end", 0.0))
        spk = label(seg.get("speaker"))
        text = seg.get("text", "").strip()
        lines.append(f"[{start} - {end}] {spk}: {text}")

    dst.write_text("\n".join(lines) + "\n")
    print(f"speakers mapped: {speaker_map}", file=sys.stderr)
    print(f"segments: {len(segments)}", file=sys.stderr)


if __name__ == "__main__":
    main()
