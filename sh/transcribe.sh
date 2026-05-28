#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
AUDIO_DIR="$BASE/audio"
TRANSCRIPT_DIR="$BASE/transcripts"
GLOSSARY_DIR="$BASE/glossary"
VENV="$BASE/.venv"
DIARIZATION_VENV="${DIARIZATION_VENV:-$BASE/.venv-diar-test}"
DIARIZATION_MODEL="${DIARIZATION_MODEL:-pyannote/speaker-diarization-community-1}"
DIARIZATION_NUM_SPEAKERS="${DIARIZATION_NUM_SPEAKERS:-2}"
DIARIZATION_EXCLUSIVE="${DIARIZATION_EXCLUSIVE:-1}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

# ssh non-interactive 세션에서 Homebrew 경로가 PATH에 없을 수 있으므로 상단에서 보장.
# ffmpeg, python 등 모든 외부 바이너리에 영향.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
# CPU 병렬성 풀활용 (numba/OMP/MKL/VECLIB 백엔드).
CORES="$(sysctl -n hw.ncpu 2>/dev/null || echo 8)"
export OMP_NUM_THREADS="$CORES" MKL_NUM_THREADS="$CORES" NUMEXPR_MAX_THREADS="$CORES" VECLIB_MAXIMUM_THREADS="$CORES" NUMBA_NUM_THREADS="$CORES"
# Demucs가 PyTorch MPS에서 "Output channels > 65536" 같은 미지원 연산을 만나도 전체 실패하지
# 않고 해당 op만 CPU로 fallback하도록 허용. 나머지 연산은 MPS GPU에서 실행.
export PYTORCH_ENABLE_MPS_FALLBACK=1
# Homebrew Python can miss the system trust store in non-interactive runs. Demucs
# downloads model weights through urllib/torch.hub, so point it at certifi.
if [ -z "${SSL_CERT_FILE:-}" ] && [ -x "$VENV/bin/python" ]; then
  if cert_path="$("$VENV/bin/python" -m certifi 2>/dev/null)"; then
    export SSL_CERT_FILE="$cert_path"
    export REQUESTS_CA_BUNDLE="$cert_path"
  fi
fi

initial_prompt=""
if [ -s "$GLOSSARY_DIR/glossary_prompt.txt" ]; then
  initial_prompt="$(cat "$GLOSSARY_DIR/glossary_prompt.txt")"
fi
# whisperx 3.3.x는 --hotwords 미지원. 용어집은 initial_prompt로만 주입한다.

set -a
source "$BASE/.env"
set +a

if [ -z "${HF_TOKEN:-}" ]; then
  echo "HF_TOKEN not set in $BASE/.env" >&2
  exit 1
fi
if [ ! -x "$DIARIZATION_VENV/bin/python" ]; then
  echo "Diarization venv not found: $DIARIZATION_VENV" >&2
  echo "Create it with: /opt/homebrew/bin/python3.11 -m venv $DIARIZATION_VENV && $DIARIZATION_VENV/bin/python -m pip install 'pyannote.audio>=4.0,<4.1'" >&2
  exit 1
fi

# 프로젝트 서브디렉터리는 .env의 MEETING_PROJECTS로 정의. `unsorted/`는 자동 전사 안 함.
read -r -a PROJECTS <<<"${MEETING_PROJECTS:-worxphere}"

transcribed=0
skipped=0

shopt -s nullglob
for proj in "${PROJECTS[@]}"; do
  in_dir="$AUDIO_DIR/$proj"
  out_dir="$TRANSCRIPT_DIR/$proj"
  [ -d "$in_dir" ] || continue
  mkdir -p "$out_dir"

  for audio in "$in_dir"/*.m4a; do
    name="$(basename "$audio" .m4a)"
    out="$out_dir/$name.txt"

    if [ -f "$out" ]; then
      skipped=$((skipped + 1))
      continue
    fi

    echo "transcribing: $proj/$name.m4a"

    # [1/3] 고음질 stereo wav로 변환 (Demucs는 44.1kHz stereo 선호)
    raw="$TMP_DIR/$name.raw.wav"
    ffmpeg -loglevel error -y -i "$audio" -ar 44100 -ac 2 -c:a pcm_s16le "$raw"

    # pyannote diarization은 speaker embedding 보존이 중요하므로 원본에 가까운
    # 16k mono를 사용한다. Demucs/EQ/denoise/loudnorm은 전사 품질용으로만 적용.
    diar_wav="$TMP_DIR/$name.diar.wav"
    ffmpeg -loglevel error -y -i "$audio" -ar 16000 -ac 1 -c:a pcm_s16le "$diar_wav"

    # [2/3] Demucs로 보컬(음성)만 분리 — 통화 녹음에서 잡음/에코 제거.
    # Apple Silicon MPS 백엔드로 가속 (M4 Max GPU).
    demucs_out="$TMP_DIR/demucs"
    mkdir -p "$demucs_out"
    echo "  >> demucs voice separation..."
    "$VENV/bin/demucs" --two-stems=vocals -d mps -o "$demucs_out" "$raw" >/dev/null 2>&1 || \
      "$VENV/bin/demucs" --two-stems=vocals -d cpu -o "$demucs_out" "$raw"
    vocals="$demucs_out/htdemucs/$name.raw/vocals.wav"

    # [3/3] 16kHz mono로 다운샘플 + 음성대역 EQ + 노이즈 감쇠 + 볼륨 정규화
    wav="$TMP_DIR/$name.wav"
    ffmpeg -loglevel error -y -i "$vocals" \
      -af "highpass=f=80,lowpass=f=8000,afftdn=nr=15,loudnorm=I=-16:LRA=11:TP=-1.5" \
      -ar 16000 -ac 1 -c:a pcm_s16le "$wav"

    # Apple Silicon Metal GPU 활용 경로: mlx-whisper로 word timestamp 전사.
    mlx_args=(
      "$wav"
      --model mlx-community/whisper-large-v3-mlx
      --language ko
      --output-dir "$TMP_DIR"
    )
    [ -n "$initial_prompt" ] && mlx_args+=(--initial-prompt "$initial_prompt")

    "$VENV/bin/python" "$BASE/sh/transcribe_mlx.py" "${mlx_args[@]}"

    # pyannote Community-1 공식 권장 경로는 pyannote.audio 4.x와 exclusive diarization.
    # Whisper/MLX 의존성과 충돌하지 않도록 별도 venv에서 화자 할당만 수행한다.
    diarized_json="$TMP_DIR/$name.diarized.json"
    diar_args=(
      "$TMP_DIR/$name.json"
      "$diar_wav"
      "$diarized_json"
      --model "$DIARIZATION_MODEL"
      --num-speakers "$DIARIZATION_NUM_SPEAKERS"
    )
    [ "$DIARIZATION_EXCLUSIVE" = "1" ] && diar_args+=(--exclusive)
    "$DIARIZATION_VENV/bin/python" "$BASE/sh/assign_speakers_pyannote.py" "${diar_args[@]}"

    "$VENV/bin/python" "$BASE/sh/format_transcript.py" \
      "$diarized_json" \
      "$out"

    transcribed=$((transcribed + 1))
  done
done

echo "---"
echo "transcribed: $transcribed, skipped (already exists): $skipped"
echo "destination: $TRANSCRIPT_DIR/{${MEETING_PROJECTS// /,}}"
