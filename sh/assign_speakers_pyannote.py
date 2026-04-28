#!/usr/bin/env python3
"""Assign pyannote diarization speakers to a Whisper JSON transcript.

Uses pyannote Community-1's exclusive diarization output when available. The
result keeps the WhisperX-compatible `segments` shape consumed by
format_transcript.py, but splits segments when word-level speaker changes are
detected.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import torch
from pyannote.audio import Pipeline


def load_pipeline(model: str, token: str | None) -> Pipeline:
    try:
        return Pipeline.from_pretrained(model, token=token)
    except TypeError:
        # pyannote.audio 3.x compatibility.
        return Pipeline.from_pretrained(model, use_auth_token=token)


def pick_annotation(output, exclusive: bool):
    if exclusive and hasattr(output, "exclusive_speaker_diarization"):
        return output.exclusive_speaker_diarization
    if hasattr(output, "speaker_diarization"):
        return output.speaker_diarization
    return output


def assign_speaker(start: float, end: float, diarization) -> str | None:
    mid = (start + end) / 2.0
    best_speaker = None
    best_overlap = 0.0
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        if turn.start <= mid <= turn.end:
            return str(speaker)
        overlap = max(0.0, min(end, turn.end) - max(start, turn.start))
        if overlap > best_overlap:
            best_overlap = overlap
            best_speaker = str(speaker)
    return best_speaker


def word_text(word: dict) -> str:
    return str(word.get("word") or word.get("text") or "")


def joined_words(words: list[dict]) -> str:
    pieces = [word_text(word) for word in words if word_text(word)]
    if not pieces:
        return ""
    compact = "".join(pieces).strip()
    spaced = " ".join(piece.strip() for piece in pieces if piece.strip())
    if len(words) > 1 and " " not in compact and spaced:
        return spaced
    return compact


def flush_group(group: list[dict], fallback_segment: dict | None = None) -> dict | None:
    if not group:
        return None
    first = group[0]
    last = group[-1]
    speaker = first.get("speaker")
    text = joined_words(group)
    if not text and fallback_segment:
        text = str(fallback_segment.get("text", "")).strip()
    return {
        "start": float(first.get("start", fallback_segment.get("start", 0.0) if fallback_segment else 0.0)),
        "end": float(last.get("end", fallback_segment.get("end", 0.0) if fallback_segment else 0.0)),
        "text": text,
        "speaker": speaker,
        "words": group,
    }


def assign_segments(segments: list[dict], diarization, split: bool) -> list[dict]:
    assigned: list[dict] = []
    for segment in segments:
        words = [
            dict(word)
            for word in (segment.get("words") or [])
            if "start" in word and "end" in word
        ]
        if not words:
            item = dict(segment)
            item["speaker"] = assign_speaker(
                float(segment.get("start", 0.0)),
                float(segment.get("end", 0.0)),
                diarization,
            )
            assigned.append(item)
            continue

        for word in words:
            word["speaker"] = assign_speaker(float(word["start"]), float(word["end"]), diarization)

        if not split:
            item = dict(segment)
            item["words"] = words
            counts: dict[str, int] = {}
            for word in words:
                speaker = word.get("speaker")
                if speaker:
                    counts[speaker] = counts.get(speaker, 0) + 1
            item["speaker"] = max(counts, key=counts.get) if counts else None
            assigned.append(item)
            continue

        current: list[dict] = []
        current_speaker = object()
        for word in words:
            speaker = word.get("speaker")
            if current and speaker != current_speaker:
                chunk = flush_group(current, segment)
                if chunk:
                    assigned.append(chunk)
                current = []
            current_speaker = speaker
            current.append(word)
        chunk = flush_group(current, segment)
        if chunk:
            assigned.append(chunk)

    return assigned


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input_json")
    ap.add_argument("audio")
    ap.add_argument("output_json")
    ap.add_argument("--model", default="pyannote/speaker-diarization-community-1")
    ap.add_argument("--token-env", default="HF_TOKEN")
    ap.add_argument("--num-speakers", type=int)
    ap.add_argument("--min-speakers", type=int)
    ap.add_argument("--max-speakers", type=int)
    ap.add_argument("--exclusive", action="store_true")
    ap.add_argument("--no-split", action="store_true")
    ap.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    args = ap.parse_args()

    data = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    token = os.environ.get(args.token_env)

    print(f">> diarizing ({args.model})...", file=sys.stderr)
    pipeline = load_pipeline(args.model, token)

    device = args.device
    if device == "auto":
        if torch.cuda.is_available():
            device = "cuda"
        elif torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"
    if device != "cpu":
        pipeline.to(torch.device(device))
        print(f">> pyannote on {device}", file=sys.stderr)

    diar_kwargs = {}
    if args.num_speakers is not None:
        diar_kwargs["num_speakers"] = args.num_speakers
    else:
        if args.min_speakers is not None:
            diar_kwargs["min_speakers"] = args.min_speakers
        if args.max_speakers is not None:
            diar_kwargs["max_speakers"] = args.max_speakers

    diarization_output = pipeline(args.audio, **diar_kwargs)
    diarization = pick_annotation(diarization_output, args.exclusive)

    print(">> assigning speakers...", file=sys.stderr)
    data["segments"] = assign_segments(data.get("segments", []), diarization, split=not args.no_split)
    data["diarization"] = {
        "model": args.model,
        "exclusive": bool(args.exclusive),
        "split_on_speaker_change": not args.no_split,
    }

    out = Path(args.output_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f">> saved {out}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise
