#!/usr/bin/env python3
"""Run a pyannote diarization model and write normalized JSON segments."""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import torch
from pyannote.audio import Pipeline


def load_pipeline(model: str, token: str | None) -> Pipeline:
    try:
        return Pipeline.from_pretrained(model, token=token)
    except TypeError:
        return Pipeline.from_pretrained(model, use_auth_token=token)


def pick_annotation(output, exclusive: bool):
    if exclusive and hasattr(output, "exclusive_speaker_diarization"):
        return output.exclusive_speaker_diarization
    if hasattr(output, "speaker_diarization"):
        return output.speaker_diarization
    return output


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--model", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--token-env", default="HF_TOKEN")
    ap.add_argument("--num-speakers", type=int)
    ap.add_argument("--min-speakers", type=int)
    ap.add_argument("--max-speakers", type=int)
    ap.add_argument("--exclusive", action="store_true")
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "mps", "cuda"])
    args = ap.parse_args()

    token = os.environ.get(args.token_env)
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

    diar_kwargs = {}
    if args.num_speakers is not None:
        diar_kwargs["num_speakers"] = args.num_speakers
    else:
        if args.min_speakers is not None:
            diar_kwargs["min_speakers"] = args.min_speakers
        if args.max_speakers is not None:
            diar_kwargs["max_speakers"] = args.max_speakers

    output = pipeline(args.audio, **diar_kwargs)
    annotation = pick_annotation(output, args.exclusive)

    segments = []
    stats = defaultdict(lambda: {"turns": 0, "seconds": 0.0})
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        start = float(turn.start)
        end = float(turn.end)
        segments.append({"start": start, "end": end, "speaker": str(speaker)})
        stats[str(speaker)]["turns"] += 1
        stats[str(speaker)]["seconds"] += max(0.0, end - start)

    payload = {
        "audio": str(args.audio),
        "model": args.model,
        "exclusive": bool(args.exclusive),
        "device": device,
        "speaker_count": len(stats),
        "turn_count": len(segments),
        "speakers": {
            speaker: {
                "turns": data["turns"],
                "seconds": round(data["seconds"], 3),
            }
            for speaker, data in sorted(stats.items())
        },
        "segments": segments,
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ["model", "exclusive", "device", "speaker_count", "turn_count", "speakers"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise
