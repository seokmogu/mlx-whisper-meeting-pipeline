#!/usr/bin/env python3
"""Shadow A/B: full-file MLX Whisper versus diarization-derived speech clips."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import wave
from difflib import SequenceMatcher
from pathlib import Path


def build_clips(
    diarization_json: Path,
    *,
    audio_duration: float,
    pad_seconds: float,
    merge_gap_seconds: float,
) -> list[tuple[float, float]]:
    payload = json.loads(diarization_json.read_text(encoding="utf-8"))
    spans = sorted(
        (
            max(0.0, float(item["start"]) - pad_seconds),
            min(audio_duration, float(item["end"]) + pad_seconds),
        )
        for item in payload.get("segments", [])
        if float(item["end"]) >= float(item["start"])
    )
    merged: list[list[float]] = []
    for start, end in spans:
        if not merged or start > merged[-1][1] + merge_gap_seconds:
            merged.append([start, end])
            continue
        merged[-1][1] = max(merged[-1][1], end)
    return [(round(start, 3), round(end, 3)) for start, end in merged if end > start]


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as audio:
        return audio.getnframes() / audio.getframerate()


def transcribe(audio: Path, model: str, clip_timestamps: str | list[float]) -> tuple[dict, float]:
    import mlx_whisper

    started = time.perf_counter()
    result = mlx_whisper.transcribe(
        str(audio),
        path_or_hf_repo=model,
        language="ko",
        word_timestamps=True,
        condition_on_previous_text=False,
        hallucination_silence_threshold=1.5,
        no_speech_threshold=0.6,
        logprob_threshold=-1.0,
        compression_ratio_threshold=2.4,
        clip_timestamps=clip_timestamps,
    )
    return result, time.perf_counter() - started


def transcript_text(result: dict) -> str:
    return " ".join(str(segment.get("text") or "").strip() for segment in result.get("segments", [])).strip()


def word_intervals(result: dict) -> list[tuple[float, float]]:
    intervals = []
    for segment in result.get("segments", []):
        for word in segment.get("words") or []:
            if "start" in word and "end" in word:
                intervals.append((float(word["start"]), float(word["end"])))
    return intervals


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def _inside_clips(start: float, end: float, clips: list[tuple[float, float]]) -> bool:
    midpoint = (start + end) / 2.0
    return any(clip_start <= midpoint <= clip_end for clip_start, clip_end in clips)


def _count_high_risk(text: str) -> dict[str, int]:
    return {
        "arabic_number_tokens": len(re.findall(r"(?<!\w)\d+(?:[.,]\d+)?(?!\w)", text)),
        "negation_markers": len(re.findall(r"(?:^|\s)(?:안|못)(?=\s)|없|아니", text)),
    }


def safe_payload(result: dict) -> dict:
    return {
        "segments": result.get("segments", []),
        "language": result.get("language", "ko"),
    }


def compare(full: dict, clipped: dict, clips: list[tuple[float, float]], *, full_seconds: float, clipped_seconds: float, audio_duration: float) -> dict:
    full_text = transcript_text(full)
    clipped_text = transcript_text(clipped)
    full_normalized = normalize(full_text)
    clipped_normalized = normalize(clipped_text)
    full_words = word_intervals(full)
    clipped_words = word_intervals(clipped)
    outside = [(start, end) for start, end in full_words if not _inside_clips(start, end, clips)]
    clip_seconds = sum(end - start for start, end in clips)
    return {
        "audio_duration_seconds": round(audio_duration, 3),
        "clip_count": len(clips),
        "clip_seconds": round(clip_seconds, 3),
        "clip_coverage_ratio": round(clip_seconds / max(audio_duration, 1e-9), 6),
        "full_runtime_seconds": round(full_seconds, 6),
        "clipped_runtime_seconds": round(clipped_seconds, 6),
        "runtime_speedup": round(full_seconds / max(clipped_seconds, 1e-9), 3),
        "full_word_count": len(full_words),
        "clipped_word_count": len(clipped_words),
        "full_char_count": len(full_normalized),
        "clipped_char_count": len(clipped_normalized),
        "normalized_char_similarity": round(
            SequenceMatcher(None, full_normalized, clipped_normalized, autojunk=False).ratio(),
            6,
        ),
        "full_words_outside_vad_clips": len(outside),
        "outside_vad_intervals": [
            {"start": round(start, 3), "end": round(end, 3)} for start, end in outside[:100]
        ],
        "outside_vad_intervals_truncated": len(outside) > 100,
        "high_risk_counts": {
            "full": _count_high_risk(full_text),
            "clipped": _count_high_risk(clipped_text),
        },
        "full_text_sha256": hashlib.sha256(full_text.encode("utf-8")).hexdigest(),
        "clipped_text_sha256": hashlib.sha256(clipped_text.encode("utf-8")).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("diarization_json", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="mlx-community/whisper-large-v3-mlx")
    parser.add_argument("--pad-seconds", type=float, default=0.25)
    parser.add_argument("--merge-gap-seconds", type=float, default=0.5)
    args = parser.parse_args()

    duration = wav_duration(args.audio)
    clips = build_clips(
        args.diarization_json,
        audio_duration=duration,
        pad_seconds=args.pad_seconds,
        merge_gap_seconds=args.merge_gap_seconds,
    )
    clip_timestamps = [value for clip in clips for value in clip]
    if not clip_timestamps:
        raise SystemExit("no speech clips found")

    # Load/compile the model before measuring either branch.
    transcribe(args.audio, args.model, [0.0, min(1.0, duration)])
    full, full_seconds = transcribe(args.audio, args.model, "0")
    clipped, clipped_seconds = transcribe(args.audio, args.model, clip_timestamps)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "full.json").write_text(
        json.dumps(safe_payload(full), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "clipped.json").write_text(
        json.dumps(safe_payload(clipped), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report = {
        "experiment": "vad-clip-shadow-v1",
        "production_code_modified": False,
        "model": args.model,
        "pad_seconds": args.pad_seconds,
        "merge_gap_seconds": args.merge_gap_seconds,
        **compare(
            full,
            clipped,
            clips,
            full_seconds=full_seconds,
            clipped_seconds=clipped_seconds,
            audio_duration=duration,
        ),
    }
    (args.output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
