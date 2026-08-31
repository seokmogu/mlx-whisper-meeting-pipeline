#!/usr/bin/env python3
"""Run Community-1 once and export regular plus exclusive shadow JSON."""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sh"))

from assign_speakers_pyannote import load_pipeline  # noqa: E402


def serialize(annotation) -> dict:
    segments = [
        {
            "start": round(float(turn.start), 6),
            "end": round(float(turn.end), 6),
            "speaker": str(speaker),
        }
        for turn, _, speaker in annotation.itertracks(yield_label=True)
    ]
    speakers = Counter(item["speaker"] for item in segments)
    return {
        "segments": segments,
        "speaker_count": len(speakers),
        "turn_count": len(segments),
        "turns_per_speaker": dict(sorted(speakers.items())),
    }


def write_payload(path: Path, payload: dict, *, model: str, exclusive: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "model": model,
                "exclusive": exclusive,
                **payload,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("--regular-output", type=Path, required=True)
    parser.add_argument("--exclusive-output", type=Path, required=True)
    parser.add_argument("--model", default="pyannote/speaker-diarization-community-1")
    parser.add_argument("--token-env", default="HF_TOKEN")
    parser.add_argument("--min-speakers", type=int, default=2)
    parser.add_argument("--max-speakers", type=int, default=5)
    parser.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    args = parser.parse_args()

    pipeline = load_pipeline(args.model, os.environ.get(args.token_env))
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

    output = pipeline(
        str(args.audio),
        min_speakers=args.min_speakers,
        max_speakers=args.max_speakers,
    )
    regular = serialize(output.speaker_diarization)
    exclusive = serialize(output.exclusive_speaker_diarization)
    write_payload(args.regular_output, regular, model=args.model, exclusive=False)
    write_payload(args.exclusive_output, exclusive, model=args.model, exclusive=True)
    print(
        json.dumps(
            {
                "device": device,
                "regular": {key: regular[key] for key in ("speaker_count", "turn_count", "turns_per_speaker")},
                "exclusive": {key: exclusive[key] for key in ("speaker_count", "turn_count", "turns_per_speaker")},
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
