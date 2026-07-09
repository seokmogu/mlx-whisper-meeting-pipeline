#!/usr/bin/env python3
"""Format WhisperX/diarization JSON into a readable timestamped speaker transcript.

Post-processing applied:
  1. Drop segments whose text is empty or pure noise (e.g. ``Q.``, ``..``, music marks).
  2. Merge consecutive segments by the same speaker when the gap between them
     is small (default ``--merge-gap 2.0``s) — keeps natural pauses inside a
     turn from being split into many short lines.
  3. Skip segments shorter than ``--min-chars`` characters (default 0).
"""
import argparse
import json
import sys
from pathlib import Path


def fmt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


_NOISE_TOKENS = {
    "", ".", "..", "...", "?", "!",
    "Q.", "q.", "Q", "q",
    "음", "어", "아", "응",
    "[음악]", "[Music]", "♪", "♫", "MBC", "BGM",
}


def is_noise(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return True
    return s in _NOISE_TOKENS


def filter_and_merge(segments, merge_gap: float, min_chars: int):
    cleaned = []
    for seg in segments:
        text = str(seg.get("text", "")).strip()
        if is_noise(text):
            continue
        if len(text) < min_chars:
            continue
        cleaned.append({
            "start": float(seg.get("start", 0.0)),
            "end": float(seg.get("end", 0.0)),
            "speaker": seg.get("speaker"),
            "text": text,
        })

    merged = []
    for seg in cleaned:
        if (
            merged
            and merged[-1]["speaker"] == seg["speaker"]
            and seg["start"] - merged[-1]["end"] <= merge_gap
        ):
            merged[-1]["end"] = seg["end"]
            joiner = "" if merged[-1]["text"].endswith((" ", "\n")) else " "
            merged[-1]["text"] = merged[-1]["text"] + joiner + seg["text"]
        else:
            merged.append(dict(seg))
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_json")
    ap.add_argument("output_txt")
    ap.add_argument("--merge-gap", type=float, default=2.0,
                    help="Merge consecutive same-speaker segments separated by ≤ this many seconds.")
    ap.add_argument("--min-chars", type=int, default=0,
                    help="Drop segments whose text has fewer than this many characters.")
    args = ap.parse_args()

    src, dst = Path(args.input_json), Path(args.output_txt)
    data = json.loads(src.read_text())
    segments = filter_and_merge(
        data.get("segments", []),
        merge_gap=args.merge_gap,
        min_chars=args.min_chars,
    )

    speaker_map: dict[str, str] = {}

    def label(speaker_id):
        if speaker_id is None:
            return "?"
        if speaker_id not in speaker_map:
            # A..Z for the first 26 speakers, then S27, S28, … — never runs out
            # (the old iter("ABCDEFGHIJ") raised StopIteration past 10 speakers,
            # crashing the whole transcript).
            idx = len(speaker_map)
            speaker_map[speaker_id] = chr(ord("A") + idx) if idx < 26 else f"S{idx + 1}"
        return speaker_map[speaker_id]

    lines = []
    for seg in segments:
        start = fmt_time(seg["start"])
        end = fmt_time(seg["end"])
        spk = label(seg.get("speaker"))
        lines.append(f"[{start} - {end}] {spk}: {seg['text']}")

    dst.write_text("\n".join(lines) + "\n")
    print(f"speakers mapped: {speaker_map}", file=sys.stderr)
    print(f"segments: {len(segments)} (after filter+merge)", file=sys.stderr)


if __name__ == "__main__":
    main()
