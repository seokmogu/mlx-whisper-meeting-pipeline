# 화자-단어 할당 shadow 실험

운영 코드와 운영 산출물을 변경하지 않고 다음 두 방식을 비교한다.

- 기준선: `sh/assign_speakers_pyannote.py`의 현재 `assign_speaker`를 그대로 import
- 후보: 모든 화자 구간과 단어 구간의 실제 겹친 시간을 합산하고, 동시발화·근소한 화자 전환을 `uncertain`으로 표시하는 순차 인덱스

후보는 `experiments/speaker-assignment/` 안에만 있으며 `sh/transcribe.sh`, 원본 오디오, 운영 전사, 회의록을 쓰지 않는다. 실험 출력은 `out/`에 저장되고 git에서 제외된다. 보고서에는 전사 문장을 넣지 않고 시간과 화자 라벨 통계만 남긴다.

## 합성 fixture와 90분 성능 비교

```bash
.venv-diar-test/bin/python -m unittest \
  experiments/speaker-assignment/test_compare_assignment.py

.venv-diar-test/bin/python \
  experiments/speaker-assignment/compare_assignment.py \
  --output experiments/speaker-assignment/out/synthetic-report.json
```

## 기존 음원의 격리 산출물 비교

`transcript.json`은 mlx-whisper의 word timestamp JSON, `diarization.json`은 `experiments/diarization/diarize_pyannote.py`의 JSON 형식이어야 한다.

`export_diarizations.py`는 Community-1을 한 번만 실행해 일반 결과와 exclusive 결과를 함께 내보낸다. 운영 화자분리 파일은 생성하거나 덮어쓰지 않는다.

```bash
.venv-diar-test/bin/python \
  experiments/speaker-assignment/compare_assignment.py \
  --transcript-json experiments/speaker-assignment/out/pilot/transcript.json \
  --diarization-json experiments/speaker-assignment/out/pilot/diarization.json \
  --output experiments/speaker-assignment/out/pilot/report.json
```

실제 음원 결과에는 정답 화자 이름이 없으므로 속도·변경량·불확실 구간만 판정할 수 있다. 운영 승격 전에는 변경 구간의 음성을 직접 듣고, 숫자·부정어·담당자·기한·고유명사에 영향이 없는지 확인해야 한다.

## 승격 게이트

- 합성 fixture 전부 통과
- 후보 알고리즘과 단순 전수 reference 결과가 동일
- 실제 입력에서 미할당 단어가 증가하지 않음
- 동시발화·근소한 화자 전환만 불확실로 표시
- 사람 청취 표본에서 잘못된 담당자·발화자 확정이 증가하지 않음
- 운영 파일 SHA-256 불변

## VAD clip shadow A/B

일반 diarization의 발화 구간을 0.25초 패딩하고 0.5초 이하 간격을 합친 뒤, mlx-whisper의 `clip_timestamps`로 전달한다. 모델을 먼저 1초 warm-up한 다음 전체 파일과 clip 전사를 같은 프로세스에서 비교한다.

```bash
.venv/bin/python \
  experiments/speaker-assignment/vad_clip_experiment.py \
  experiments/speaker-assignment/out/pilot/audio.16k.wav \
  experiments/speaker-assignment/out/pilot/diarization-regular.json \
  --output-dir experiments/speaker-assignment/out/pilot/vad
```

보고서에는 처리시간, 단어·문자 수, 정규화 문자열 유사도, VAD 바깥 단어 수와 해시만 기록한다. 전체·clip 전사 본문은 git에서 제외되는 `out/`에만 저장한다.

## ASR 모델 shadow A/B

VAD A/B에서 생성한 동일 입력의 full 결과를 기준으로, candidate 모델만 warm-up 후 다시 전사한다. 보고서에는 본문 대신 속도·크기·문자 유사도·숫자/부정 표현/ASCII 용어 개수와 해시만 남긴다.

```bash
.venv/bin/python \
  experiments/speaker-assignment/asr_model_experiment.py \
  experiments/speaker-assignment/out/pilot/audio.16k.wav \
  experiments/speaker-assignment/out/pilot/vad/full.json \
  --baseline-runtime-seconds 80.712053 \
  --output-dir experiments/speaker-assignment/out/pilot/turbo
```
