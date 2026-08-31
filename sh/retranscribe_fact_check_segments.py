#!/usr/bin/env python3
"""Re-transcribe only STT-risk claim intervals from a meeting audio file."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import mlx_whisper


CHECKED_VERDICTS = {"verified", "contradicted", "partially_verified"}


def build_items(candidates: dict, evidence: dict, *, padding: float) -> list[dict]:
    evidence_by_id = {row["candidate_id"]: row for row in evidence.get("results", [])}
    rows = []
    for candidate in candidates.get("candidates", []):
        result = evidence_by_id.get(candidate.get("id"))
        if not candidate.get("stt_risk") or not result or result.get("verdict") not in CHECKED_VERDICTS:
            continue
        rows.append(
            {
                "candidate_id": candidate["id"],
                "claim_text": candidate["claim_text"],
                "clip_start": max(0.0, float(candidate["start_seconds"]) - padding),
                "clip_end": float(candidate["end_seconds"]) + padding,
            }
        )
    return rows


def transcribe_intervals(audio: Path, rows: list[dict], *, model: str, language: str) -> list[dict]:
    if not rows:
        return []
    clip_timestamps = [value for row in rows for value in (row["clip_start"], row["clip_end"])]
    result = mlx_whisper.transcribe(
        str(audio),
        path_or_hf_repo=model,
        language=language,
        word_timestamps=True,
        condition_on_previous_text=False,
        clip_timestamps=clip_timestamps,
        hallucination_silence_threshold=1.5,
        no_speech_threshold=0.6,
        logprob_threshold=-1.0,
        compression_ratio_threshold=2.4,
    )
    segments = result.get("segments", [])
    output = []
    for row in rows:
        texts = []
        for segment in segments:
            start = float(segment.get("start", 0.0))
            end = float(segment.get("end", start))
            if end >= row["clip_start"] and start <= row["clip_end"]:
                value = str(segment.get("text", "")).strip()
                if value:
                    texts.append(value)
        text = " ".join(texts).strip()
        output.append(
            {
                **row,
                "retranscript_text": text,
                "retranscript_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="mlx-community/whisper-large-v3-mlx")
    parser.add_argument("--language", default="ko")
    parser.add_argument("--padding", type=float, default=4.0)
    args = parser.parse_args()
    candidates = json.loads(args.candidates.read_text(encoding="utf-8"))
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    rows = build_items(candidates, evidence, padding=max(0.0, args.padding))
    output = {
        "version": 1,
        "audio_path": str(args.audio.resolve()),
        "audio_sha256": hashlib.sha256(args.audio.read_bytes()).hexdigest(),
        "items": transcribe_intervals(args.audio, rows, model=args.model, language=args.language),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"objective fact-check audio intervals: {len(output['items'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
