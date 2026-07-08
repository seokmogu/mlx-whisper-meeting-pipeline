#!/usr/bin/env python3
"""Apple Silicon용 Whisper 전사 단계.

mlx-whisper로 Metal GPU 기반 전사를 돌려 WhisperX 호환 JSON 구조를 출력한다.
화자 분리는 pyannote 4.x 전용 venv에서 `assign_speakers_pyannote.py`가 수행한다.

Output schema (WhisperX 호환):
{
  "segments": [
    {"start": float, "end": float, "text": str, "words": [...]},
    ...
  ],
  "language": "ko"
}
"""
import argparse
import json
import sys
from pathlib import Path

import mlx_whisper


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--model", default="mlx-community/whisper-large-v3-mlx")
    ap.add_argument("--language", default="ko")
    ap.add_argument("--initial-prompt", default="")
    ap.add_argument("--output-dir", required=True)
    # Hallucination guards. hallucination_silence_threshold only takes effect
    # with word_timestamps=True (already on below) — it skips silent spans at
    # least this long once a hallucination is detected, which is what actually
    # stops the "하면 하면"/"음 음 음" repeat loops on phone-disconnect audio.
    ap.add_argument("--hallucination-silence-threshold", type=float, default=1.5)
    ap.add_argument("--no-speech-threshold", type=float, default=0.6)
    ap.add_argument("--logprob-threshold", type=float, default=-1.0)
    ap.add_argument("--compression-ratio-threshold", type=float, default=2.4)
    # Backward-compatible no-op arguments. Diarization moved to assign_speakers_pyannote.py.
    ap.add_argument("--hf-token", default="")
    ap.add_argument("--min-speakers", type=int)
    ap.add_argument("--max-speakers", type=int)
    args = ap.parse_args()

    audio_path = args.audio
    name = Path(audio_path).stem
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{name}.json"

    print(">> transcribing (mlx-whisper)...", file=sys.stderr)
    result = mlx_whisper.transcribe(
        audio_path,
        path_or_hf_repo=args.model,
        language=args.language,
        initial_prompt=args.initial_prompt or None,
        # 타임스탬프 정밀도를 0.1s 수준으로 올리기 위해 word-level alignment 켬.
        word_timestamps=True,
        condition_on_previous_text=False,
        hallucination_silence_threshold=args.hallucination_silence_threshold,
        no_speech_threshold=args.no_speech_threshold,
        logprob_threshold=args.logprob_threshold,
        compression_ratio_threshold=args.compression_ratio_threshold,
    )

    out_payload = {
        "segments": result.get("segments", []),
        "language": result.get("language", args.language),
    }
    out_path.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2))
    print(f">> saved {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
