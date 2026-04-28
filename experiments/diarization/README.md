# Diarization experiment

This experiment compares the current pyannote 3.1 diarization path with
`pyannote/speaker-diarization-community-1` without changing the production
pipeline.

## Setup on the compute host

```bash
cd ~/project/meeting-notes
test -d .venv-diar-test || /opt/homebrew/bin/python3.11 -m venv .venv-diar-test
.venv-diar-test/bin/python -m pip install --upgrade pip setuptools wheel
.venv-diar-test/bin/python -m pip install "pyannote.audio>=4.0,<4.1"
```

`speaker-diarization-community-1` requires `pyannote.audio` 4.x. The production
venv currently uses 3.x, so this experiment intentionally uses a separate venv.

## Run

```bash
cd ~/project/meeting-notes
./experiments/diarization/run_compare.sh "experiments/diarization/audio/20260428 092507.m4a" "experiments/diarization/audio/20260428 092507.txt"
```

The script writes JSON diarization output and relabeled transcript files under
`experiments/diarization/out/`.
