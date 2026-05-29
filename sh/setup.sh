#!/bin/bash
# One-time setup for the compute host (Apple Silicon Mac): Python 3.11 venvs, mlx-whisper, ffmpeg.
# Assumes macOS + Homebrew. For Linux, swap the brew commands.
#
# After running, you still need to:
#   1. Fill in .env (copy from .env.example)
#   2. Accept HuggingFace license for the pyannote models (gated):
#      - https://huggingface.co/pyannote/segmentation-3.0
#      - https://huggingface.co/pyannote/speaker-diarization-3.1
#      - https://huggingface.co/pyannote/speaker-diarization-community-1
#   3. Install the selected note LLM CLI (claude-oauth-run or codex)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"

echo "[1/4] installing system deps via brew..."
command -v brew >/dev/null || { echo "Homebrew required: https://brew.sh" >&2; exit 1; }
brew list python@3.11 >/dev/null 2>&1 || brew install python@3.11
brew list ffmpeg >/dev/null 2>&1 || brew install ffmpeg
brew list libsndfile >/dev/null 2>&1 || brew install libsndfile

echo "[2/4] creating Python 3.11 venv at $BASE/.venv..."
mkdir -p "$BASE"
if [ ! -d "$BASE/.venv" ]; then
  "$(brew --prefix)/opt/python@3.11/bin/python3.11" -m venv "$BASE/.venv"
fi

echo "[3/4] installing WhisperX + deps..."
"$BASE/.venv/bin/pip" install --upgrade pip
"$BASE/.venv/bin/pip" install whisperx mlx-whisper demucs soundfile

echo "[3a/4] prefetching Demucs default checkpoint..."
DEMUX_CACHE="$HOME/.cache/torch/hub/checkpoints"
DEMUX_CHECKPOINT="$DEMUX_CACHE/955717e8-8726e21a.th"
DEMUX_URL="https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/955717e8-8726e21a.th"
mkdir -p "$DEMUX_CACHE"
if [ ! -s "$DEMUX_CHECKPOINT" ]; then
  command -v curl >/dev/null || { echo "curl required to prefetch Demucs checkpoint" >&2; exit 1; }
  curl -L --fail -o "$DEMUX_CHECKPOINT.tmp" "$DEMUX_URL"
  mv "$DEMUX_CHECKPOINT.tmp" "$DEMUX_CHECKPOINT"
fi

echo "[3b/4] creating diarization venv for pyannote Community-1..."
if [ ! -d "$BASE/.venv-diar-test" ]; then
  "$(brew --prefix)/opt/python@3.11/bin/python3.11" -m venv "$BASE/.venv-diar-test"
fi
"$BASE/.venv-diar-test/bin/pip" install --upgrade pip setuptools wheel
"$BASE/.venv-diar-test/bin/pip" install 'pyannote.audio>=4.0,<4.1'

echo "[4/4] creating data directories..."
mkdir -p "$BASE"/{audio,transcripts,notes,glossary,logs}

echo "---"
echo "done. next steps:"
echo "  - cp .env.example .env && edit .env"
echo "  - accept pyannote HF licenses (see comments at top)"
echo "  - install the selected note LLM CLI (claude-oauth-run or codex)"
