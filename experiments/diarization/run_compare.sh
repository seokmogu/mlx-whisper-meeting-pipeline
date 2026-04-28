#!/bin/bash
set -euo pipefail

BASE="$HOME/project/meeting-notes"
EXP="$BASE/experiments/diarization"
VENV_CURRENT="$BASE/.venv"
VENV_COMMUNITY="$BASE/.venv-diar-test"

audio="${1:?usage: run_compare.sh <audio.m4a> <timestamped-transcript.txt>}"
transcript="${2:?usage: run_compare.sh <audio.m4a> <timestamped-transcript.txt>}"

mkdir -p "$EXP/audio" "$EXP/out" "$EXP/tmp"

set -a
source "$BASE/.env"
set +a

wav="$EXP/audio/$(basename "${audio%.*}" | tr ' ' '_').16k.wav"
/opt/homebrew/bin/ffmpeg -loglevel error -y -i "$audio" -ar 16000 -ac 1 -c:a pcm_s16le "$wav"

PYTORCH_ENABLE_MPS_FALLBACK=1 "$VENV_CURRENT/bin/python" "$EXP/diarize_pyannote.py" \
  "$wav" \
  --model pyannote/speaker-diarization-3.1 \
  --num-speakers 2 \
  --output "$EXP/out/pyannote_3_1_num2.json"

PYTORCH_ENABLE_MPS_FALLBACK=1 "$VENV_COMMUNITY/bin/python" "$EXP/diarize_pyannote.py" \
  "$wav" \
  --model pyannote/speaker-diarization-community-1 \
  --num-speakers 2 \
  --exclusive \
  --output "$EXP/out/community_1_num2_exclusive.json"

"$VENV_CURRENT/bin/python" "$EXP/relabel_transcript.py" \
  "$transcript" \
  "$EXP/out/pyannote_3_1_num2.json" \
  "$EXP/out/transcript_relabel_3_1.txt"

"$VENV_CURRENT/bin/python" "$EXP/relabel_transcript.py" \
  "$transcript" \
  "$EXP/out/community_1_num2_exclusive.json" \
  "$EXP/out/transcript_relabel_community_1.txt"

for f in "$transcript" "$EXP/out/transcript_relabel_3_1.txt" "$EXP/out/transcript_relabel_community_1.txt"; do
  echo "--- $f"
  awk '{ if (match($0, /\] [A-J?]:/)) { sp=substr($0, RSTART+2, 1); c[sp]++ } } END { for (s in c) print s, c[s] }' "$f" | sort
done
