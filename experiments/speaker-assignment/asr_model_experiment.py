#!/usr/bin/env python3
"""Shadow A/B for an MLX Whisper candidate model against a saved baseline."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from vad_clip_experiment import (
    _count_high_risk,
    normalize,
    safe_payload,
    transcript_text,
    transcribe,
    word_intervals,
)


def compare_models(
    baseline: dict,
    candidate: dict,
    *,
    baseline_seconds: float,
    candidate_seconds: float,
) -> dict:
    baseline_text = transcript_text(baseline)
    candidate_text = transcript_text(candidate)
    baseline_normalized = normalize(baseline_text)
    candidate_normalized = normalize(candidate_text)
    return {
        "baseline_runtime_seconds": round(baseline_seconds, 6),
        "candidate_runtime_seconds": round(candidate_seconds, 6),
        "runtime_speedup": round(baseline_seconds / max(candidate_seconds, 1e-9), 3),
        "baseline_word_count": len(word_intervals(baseline)),
        "candidate_word_count": len(word_intervals(candidate)),
        "baseline_char_count": len(baseline_normalized),
        "candidate_char_count": len(candidate_normalized),
        "normalized_char_similarity": round(
            SequenceMatcher(
                None,
                baseline_normalized,
                candidate_normalized,
                autojunk=False,
            ).ratio(),
            6,
        ),
        "high_risk_counts": {
            "baseline": _count_high_risk(baseline_text),
            "candidate": _count_high_risk(candidate_text),
        },
        "baseline_ascii_term_count": len(re.findall(r"\b[A-Za-z][A-Za-z0-9_-]+\b", baseline_text)),
        "candidate_ascii_term_count": len(re.findall(r"\b[A-Za-z][A-Za-z0-9_-]+\b", candidate_text)),
        "baseline_text_sha256": hashlib.sha256(baseline_text.encode("utf-8")).hexdigest(),
        "candidate_text_sha256": hashlib.sha256(candidate_text.encode("utf-8")).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("baseline_json", type=Path)
    parser.add_argument("--baseline-runtime-seconds", type=float, required=True)
    parser.add_argument("--candidate-model", default="mlx-community/whisper-large-v3-turbo")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    baseline = json.loads(args.baseline_json.read_text(encoding="utf-8"))
    transcribe(args.audio, args.candidate_model, [0.0, 1.0])
    candidate, candidate_seconds = transcribe(args.audio, args.candidate_model, "0")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "candidate.json").write_text(
        json.dumps(safe_payload(candidate), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report = {
        "experiment": "asr-model-shadow-v1",
        "production_code_modified": False,
        "candidate_model": args.candidate_model,
        **compare_models(
            baseline,
            candidate,
            baseline_seconds=args.baseline_runtime_seconds,
            candidate_seconds=candidate_seconds,
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
