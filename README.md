# mlx-whisper-meeting-pipeline

English | [한국어](#핵심-설계)

Local-first meeting-notes pipeline for Korean audio. The active production path watches macOS Voice Memos on this MacBook, treats every local recording as a Worxphere meeting, transcribes and diarizes it locally, generates structured Markdown notes with a selectable LLM provider, and creates meeting-context-reviewer artifacts. Notion upload and remote compute are optional paths, not part of the current automatic flow.

## English Overview

The pipeline keeps private meeting artifacts out of git while making the processing code reusable. Each meeting project is a subdirectory under `audio/`, `transcripts/`, and `notes/`, configured by `MEETING_PROJECTS`. This MacBook is currently configured as a Worxphere-only recorder: `VOICE_MEMO_FORCE_PROJECT=worxphere` sends every local Voice Memo to `audio/worxphere/` regardless of memo title.

The active loop syncs completed Voice Memos, imports manually copied phone recordings, trims/merges/quarantines audio, transcribes with `mlx-whisper`, splits speakers with `pyannote`, applies evidence-backed lexical transcript patches, generates validated Markdown notes, then runs `meeting-context-reviewer`. Incomplete note/review jobs are checkpointed and retried; canonical notes are promoted only after validation. The raw transcript is immutable. Past notes and the employee roster supply context for later runs. Current-meeting attendee hints always take precedence over participant continuity inferred from earlier meetings.

## Quick Start

```bash
cp .env.example .env
# Fill in HF_TOKEN, MEETING_PROJECTS, VOICE_MEMO_FORCE_PROJECT.

# First-time local setup:
./sh/setup.sh

# Manual local dry-run and run:
./sh/run-local-pipeline.sh --dry-run
./sh/run-local-pipeline.sh

# Automatic Voice Memos processing:
./sh/local-launchd.sh install
./sh/local-launchd.sh status
```

The launchd installer baselines existing recordings as already seen, loads the user LaunchAgent, and keeps the local path read-only with respect to Notion and Git remotes.

## Optional Notion Upload

The local Voice Memos pipeline never writes to Notion. It renders a readable preview and queues a note only when its transcript, validated canonical note, and fresh context review are complete. A readiness receipt binds those artifacts by SHA-256.

The separate publication entrypoint defaults to local preparation. Live publication requires current-conversation approval for one exact target and scope, recorded in an expiring approval file; a queue entry is not approval. See [the publication workflow](docs/NOTION_PUBLICATION_PIPELINE.md). The older `NOTION_UPLOAD_DATABASE_ID`/toolkit uploader is retained for the optional legacy remote path and is not the local workflow.

---

한국어 회의를 자동으로 정리·축적하는 로컬 우선 파이프라인.
현재 운영 소스는 이 MacBook의 macOS Voice Memos이며, 모든 녹음은 제목과 무관하게 Worxphere 회의로 처리됩니다. Notion 업로드와 원격 컴퓨트는 선택 경로이고, 자동 실행 경로에는 포함하지 않습니다.

## 핵심 설계

```
  [이 MacBook] Voice Memos 녹음
      │
      │ launchd → run-local-pipeline.sh
      ▼
  sync-voice-memos.sh + import-manual-audio.sh
      │
      ▼
  audio/worxphere/*.m4a
      │
      ▼
  prepare-audio-queue.py
      │  - 앞/뒤 비발화 trim
      │  - 중단 후 재녹음 merge
      │  - 짧은 무음/잡음 격리
      ▼
  transcribe.sh
      │  - mlx-whisper: 한국어 전사
      │  - pyannote: 화자 분리
      ▼
  transcripts/worxphere/*.txt
      │
      ▼
  correct-transcripts.sh
      │  - LLM은 짧은 JSON patch만 제안
      │  - 사전·명부 근거와 숫자/부정어/변경량 guard를 통과한 patch만 적용
      │  - 원본은 유지하고 state/corrected-transcripts/에 파생본 저장
      ▼
  make-notes.sh
      - skills/meeting-minutes/SKILL.md를 회의록 작성 계약으로 사용
      - 선택된 LLM provider가 검증된 교정본(없으면 원문), 최근 회의록, 직원명단을 읽고 회의록을 작성
      - 기존 고유명사 워싱용 Codex web_search + employee_roster.tsv + glossary 사용
      - 팩트체크는 객관 명제 후보를 검색 없이 추출하고, 개인정보를 제거한 공개 명제만 별도 Codex web_search로 검증
      - 출처 URL·정정 사실·틀린 이유를 `## 6. 객관 명제 팩트체크`로 하단에 추가
      │
      ▼
  notes/worxphere/*.md
      │
      ▼
  meeting-context-reviewer
      │
      ▼
  ../meeting-context-reviewer/reviews/<meeting-id>/

  extract_glossary.py / build_employee_roster.sh
      │
      ▼
  glossary/ (다음 run 시 transcribe/make-notes/reviewer에 주입)
```

`<project>`는 `.env`의 `MEETING_PROJECTS`로 정의 (공백 분리). 각 프로젝트는 `audio/`·`transcripts/`·`notes/` 아래 독립 서브디렉터리를 갖고 독립적으로 처리됩니다.

## 상호 프로젝트 디펜던시

이 저장소는 회의록 생산을 소유하지만, 리뷰와 근거 검색은 별도 프로젝트와 느슨하게 연결된다. 아래 의존성은 파일/SQLite 기반 read-only 연결이 기본이며, Notion/GitLab/Slack write는 로컬 자동화 경로에 포함하지 않는다.

| 연결 대상 | 이 저장소가 받는 것 | 이 저장소가 제공하는 것 | 소유 경계 |
|---|---|---|---|
| `../meeting-context-reviewer` | `review.md`, `review.json`, `wiki-update-candidates.md` 생성 기능 | `notes/worxphere/*.md`, `glossary/employee_roster.tsv` | 리뷰 판단/스코어링은 reviewer가 소유 |
| `../worxphere-data-collectors` | Slack/Notion/GitLab SQLite/FTS evidence | 없음 | 내부 데이터 수집과 index freshness는 WDC가 소유 |
| `../worxphere-internal` 또는 `macmini`의 FamilyBab 산출물 | 직원명단 source markdown | `glossary/employee_roster.tsv` | 직원 디렉토리 수집은 worxphere-internal이 소유, 이 repo는 회의용 최소 TSV만 생성 |
| `../agentic-services-docs/ax-os` | AX-OS 전략/로드맵/원칙 정본 | 회의록이 전략 리뷰의 입력이 됨 | 전략 source of truth는 ax-os 문서가 소유 |
| `notion-native-toolkit` | 선택적 Notion writer | 새 회의록 markdown | 로컬 Voice Memos 자동화에서는 사용하지 않음; Notion write는 별도 승인 필요 |

`mlx-whisper-meeting-pipeline`과 `meeting-context-reviewer`는 상호 의존한다. 이 저장소가 회의록과 직원명단을 reviewer에 넘기고, reviewer는 WDC/AX-OS/wiki를 조회해 리뷰 산출물을 만든다. reviewer가 만든 wiki 후보는 자동으로 이 저장소나 Notion에 반영되지 않는다.

## 현재 운영 플로우

현재 켜둔 운영 경로는 Voice Memos 기반 로컬 자동화다. 이 MacBook에서 녹음되는 Voice Memos는 이름과 무관하게 전부 Worxphere 회의로 간주한다. 이 경로는 회의록과 리뷰 산출물까지만 만들고, Notion 업로드와 Git push는 하지 않는다.

```
macOS Voice Memos 원본
  /Users/seokmogu/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings/*.m4a
        │
        │ launchd: com.seokmogu.voicememo-local-pipeline
        │   - WatchPaths: Voice Memos Recordings 폴더
        │   - StartInterval: 120초
        │   - entrypoint: sh/run-local-pipeline.sh
        ▼
sync-voice-memos.sh
  - 60초보다 어린 파일은 skip
  - state/voice-memos-seen.txt에 있는 기존 파일은 skip
  - VOICE_MEMO_FORCE_PROJECT=worxphere이면 제목과 무관하게 worxphere로 복사
  - Voice Memo 제목은 state/voice-memo-titles/<project>/<meeting>.txt에 보존해 현재 회의 참석자 보조 힌트로 사용
        ▼
audio/worxphere/*.m4a
        ▲
        │
manual-audio/worxphere/*.m4a
  - 폰으로 녹음해 복사한 예외 파일용 inbox
  - import-manual-audio.sh가 audio/worxphere/로 이동
        ▼
prepare-audio-queue.py
  - 앞/뒤 무음 또는 저레벨 비발화 구간을 잘라내고 원본은 state/audio-originals/에 보관
  - 중단 후 바로 다시 녹음된 인접 파일은 하나로 merge
  - 너무 짧거나 대부분 무음인 파일은 state/rejected-audio/로 격리
        ▼
transcribe.sh
  - mlx-whisper: 한국어 전사
  - pyannote: 화자 분리
        ▼
filter-low-content-transcripts.py
  - 전사 결과가 너무 짧으면 잡음/무발화 후보로 보고 격리
        ▼
transcripts/worxphere/*.txt
        ▼
correct-transcripts.sh
  - 원본 transcript는 수정하지 않음
  - LLM은 인명·제품명·조직명·약어의 최소 문자열 patch만 제안
  - 숫자·날짜·기한·부정어·긴 문장 재작성은 결정론적 검증기가 차단
  - 검증된 파생본과 accepted/rejected 감사 내역을 state/ 아래 저장
        ▼
make-notes.sh
  - Codex CLI로 회의록 생성
  - 기존 고유명사 워싱과 별도로 공개 팩트체크 명제만 분리해 Codex web_search
  - glossary + employee_roster.tsv 주입
  - 원본 transcript 파일을 덮어쓰지 않고, 보정 결과를 notes/<project>/*.md에 반영
  - state/meeting-attendees/<project>/<meeting>.txt의 사용자 확정 참석자는 직전 회의 참석자·화자 연속성보다 우선
  - state/fact-checks/<project>/<meeting>.*.json에 후보·검색근거·최종 판정을 저장
  - notes/<project>/*.md 하단에 객관 명제 팩트체크를 추가
        ▼
notes/worxphere/*.md
        ▼
meeting-context-reviewer
  - AX-OS profile
  - WDC SQLite/FTS evidence
  - employee roster 기반 사람 식별
        ▼
/Users/seokmogu/project/meeting-context-reviewer/reviews/<meeting-id>/
  - review.md
  - review.json
  - wiki-update-candidates.md
```

### 저장 위치

| 종류 | 위치 | 비고 |
|---|---|---|
| 원본 Voice Memos | `~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings/*.m4a` | macOS Voice Memos 앱이 관리 |
| 처리 대상 오디오 | `audio/<project>/*.m4a` | 현재 기본 project는 `worxphere` |
| 폰 녹음 수동 import | `manual-audio/worxphere/*.m4a` | 폰에서 복사한 `.m4a`를 넣는 inbox |
| trim 전 원본 | `state/audio-originals/<project>/` | 앞/뒤 비발화 구간 trim 전 원본 보관 |
| 중단 후 재녹음 merge 원본 | `state/audio-segments/<project>/` | merge 후 원본 segment 보관 |
| 잡음/무음 격리 | `state/rejected-audio/<project>/`, `state/rejected-transcripts/<project>/` | 자동 삭제하지 않고 격리 |
| 원본 전사 결과 | `transcripts/<project>/*.txt` | 화자 분리 반영, 후속 단계에서 절대 덮어쓰지 않음 |
| 검증된 교정본 | `state/corrected-transcripts/<project>/*.txt` | accepted lexical patch만 적용한 회의록 입력 파생본 |
| 교정 감사 내역 | `state/transcript-corrections/<project>/*.json` | 제안·수락·거절·근거·원본/교정본 SHA-256 |
| 회의록 | `notes/<project>/*.md` | Markdown 산출물 |
| 리뷰 결과 | `../meeting-context-reviewer/reviews/<meeting-id>/` | reviewer repo의 로컬 산출물 |
| 기존 녹음 baseline | `state/voice-memos-seen.txt` | 자동화 활성화 전 과거 녹음 backfill 방지 |
| 실행 로그 | `logs/local-pipeline.log` | launchd 실행도 이 파일에 기록 |

### 자동화 상태 확인

```bash
./sh/local-launchd.sh status
./sh/run-local-pipeline.sh --dry-run
./sh/import-manual-audio.sh --dry-run
tail -80 logs/local-pipeline.log
```

정상 상태의 핵심 신호는 `local-launchd.sh status`에서 `watching = 1`, 최신 실행의 `last exit code = 0`, 그리고 dry-run에서 기존 파일이 `skipped (already exists/seen)`로 잡히는 것이다. 폰 녹음은 `manual-audio/worxphere/`에 복사한 뒤 dry-run에서 `manual imported` 수를 확인한다.

실행 중복은 OS 파일 잠금으로 차단한다. `logs/local-pipeline.lock`에는 마지막 소유 PID가 남을 수 있으므로 파일 존재만으로 실행 중이라고 판단하지 않는다. 회의록·리뷰의 미완료 단계는 `state/local-pipeline/jobs.json`에 남고 다음 실행에서 재개된다. 리뷰 도구 누락, 출력 누락, 검증 실패는 0이 아닌 종료 코드로 보고한다.

과거 전사만 남은 파일을 자동으로 일괄 처리하지 않는다. 특정 기존 회의의 후속 산출물을 복구하려면 `./sh/run-local-pipeline.sh --only 'worxphere/<name>'`을 사용한다. 회의록 자체도 다시 만들려면 `--force-notes`를 추가한다. `--only`는 회의록·리뷰 범위이며, 앞단의 Voice Memos 동기화와 오디오 준비 범위는 기존과 같다.

### 중단 후 재녹음과 잡음 처리

전사 전에 `prepare-audio-queue.py`가 각 오디오의 앞/뒤 무음 또는 저레벨 비발화 구간을 감지해 대화가 있는 구간만 남긴다. 내부의 긴 침묵은 회의 흐름일 수 있으므로 제거하지 않는다. trim 전 원본은 `state/audio-originals/<project>/`에 보관한다.

Voice Memos를 중단했다가 바로 다시 녹음하면 macOS는 별도 `.m4a` 파일을 만든다. 로컬 파이프라인은 아직 전사/회의록이 없는 오디오 중 시작 시간이 가깝고 `AUDIO_PREP_MERGE_GAP_SECONDS` 이내로 이어지는 파일을 하나의 `* merged.m4a`로 합친다. 합쳐진 원본 segment는 `state/audio-segments/<project>/`에 보관한다.

마이크를 끄지 못해 무음이나 의미 없는 녹음이 남는 경우에는 두 단계로 걸러낸다. 전사 전에는 너무 짧거나 대부분 무음인 파일을 `state/rejected-audio/<project>/`로 격리한다. 전사 후에는 의미 있는 문자 수가 `MEETING_MIN_TRANSCRIPT_CHARS`보다 적은 transcript를 잡음/무발화 후보로 보고 transcript와 대응 오디오를 `state/rejected-*` 아래로 이동한다. 완전 삭제하지 않으므로 잘못 걸러진 파일은 수동 복구할 수 있다.

## 피드백 루프

각 회의가 쌓일수록 다음 회의 정확도가 좋아지도록 네 가지 경로로 학습 데이터가 누적됩니다:

1. **핫워드 bias** (`extract_glossary.py`)
   과거 노트의 `## 기타 메모`·`## 검증 완료`·`## 검증 필요`에서 고유명사를 뽑아 `glossary_hotwords.txt`·`glossary_prompt.txt` 생성.
   mlx-whisper의 `--initial-prompt`로 주입되어 다음 녹음 전사 시 제품명·팀명·인명 등의 오류율을 낮춤.

2. **웹검색 워싱** (`make-notes.sh`)
   선택된 LLM provider가 전사 오류로 의심되는 고유명사를 웹검색 도구로 검증 후 정정 → `## 검증 완료`에 `원문 → 정정 (근거)` 형태로 기록.

3. **객관 명제 팩트체크** (`run-meeting-fact-check.sh`)
   검색 없이 화자·타임스탬프가 있는 객관 명제와 상충 발언을 먼저 추출한다. 사람명·사내 문맥을 제거한 공개 명제만 별도 Codex web search에 전달하고, 직접 출처 URL·정정 사실·틀린 이유가 있는 결과를 회의록 하단에 추가한다. 의견·전략 판단·예측은 판정하지 않으며, 검색 실행 증거가 없으면 `틀림`으로 확정하지 않는다.

4. **이름 정규화** (로스터 + 회의록 작성 스킬)
   직원 명부(`build_employee_roster.sh` → FamilyBab 재직 스냅샷 + WDC `notion_users.json` 보강 + 로컬 이력 원장)와
   **누적 확정 사전**(`build_identity_ledger.py`), **자모 음성유사도 후보**(`phonetic_name_candidates.py`)를
   노트 프롬프트에 함께 주입. "성모/성문/성원" 같은 전사 변이를 `구석모` 하나로 수렴.

> 누적 확정 사전은 모든 노트의 `## 검증 완료`에서 `STT변형 → 정정` 매핑을 누적해 다음 회의에 주입하는
> 핵심 피드백 루프다. 기존 노트 32개 재생성 시 검증완료 매핑이 회의당 평균 5개 → 16.8개로 늘었다.

## 실험 요약 및 모델 선택 근거

각 단계의 대안을 실측 비교해 정한 결정(상세: [`docs/EXPERIMENTS.md`](docs/EXPERIMENTS.md)):

| 단계 | 채택 | 실측으로 기각/보류 |
| --- | --- | --- |
| 전처리 | Demucs + EQ/denoise | **DeepFilterNet3**(환각 유발·의미 반전으로 악화) |
| ASR | **mlx-whisper large-v3** (로컬·무료·프라이버시) | Qwen3-ASR·OpenAI gpt-4o-transcribe·GCP Vertex Gemini — 모두 로컬을 못 이김(영어 용어는 로컬이 우세) |
| 화자분리 | pyannote community-1, `min2/max5` 범위 | `num_speakers=2` 고정(다인 회의를 2명으로 뭉갬) |
| 이름/용어 | 누적 사전 + 자모 음성유사도 + 로스터 | ASR 교체(이름 오류는 오디오 애매성이라 어떤 ASR도 못 고침) |
| 내부 컨텍스트 | wdc 관련회의 **키워드 라벨** | 런타임 벡터검색(macmini 의존성으로 보류) |

이 선택은 위 문서에 기록된 제한된 회의·구간의 관찰에 근거한다. 시험한 이름 오류 일부는 여러 엔진에서 반복돼 문맥 기반 사후 교정을 채택했다. 전체 한국어 회의에서 특정 ASR이 항상 우수하거나 이름 오류를 해결할 수 없다는 증거는 아니다. 모델 변경 전에는 대표 녹음과 사람 정답을 사용해 화자·숫자·부정어·담당자·기한까지 별도로 평가해야 한다.

## 녹음 후 처리 전략

녹음 품질은 후처리로 보완하되, 전사와 화자 분리는 서로 다른 오디오를 사용합니다. 전사에는 Demucs 보컬 분리, EQ, denoise, loudness normalization을 적용한 음성 향상 WAV를 넣어 Whisper 인식률을 높입니다. 반대로 화자 분리에는 원본에 가까운 16k mono WAV를 넣어 speaker embedding이 훼손되지 않게 합니다. 실제 테스트에서 denoise/loudnorm까지 적용한 오디오를 pyannote에 넣으면 두 화자가 92%/8%로 무너졌고, 원본계열 16k mono에서는 31%/69%로 정상 분리되었습니다.

화자 분리는 `pyannote/speaker-diarization-community-1`의 exclusive diarization을 사용합니다. 이 결과를 `mlx-whisper`의 word timestamp에 매칭해 화자가 바뀌는 지점에서 transcript segment를 다시 쪼갭니다. 이후 선택된 LLM provider가 roster, glossary, 웹검색 도구를 이용해 이름·고유명사·전사 오류를 보정하고 최종 미팅노트를 생성합니다.

## 설정

### 저장소 구조
- `sh/` — 실행 스크립트 + Python 후처리
- `launchd/` — Voice Memos 감시 자동 트리거 plist 템플릿
- `.env.example` — 환경 변수 템플릿
- `experiments/diarization/` — diarization 모델 비교(3.1 vs community-1) 실험 코드

### 로컬 (녹음 머신)
```bash
cp .env.example .env
# .env에 채워야 할 것:
#   HF_TOKEN                 — pyannote gated 모델 접근용
#   MEETING_PROJECTS         — 프로젝트 서브디렉터리 (공백 분리)
#   VOICE_MEMO_FORCE_PROJECT — 이 MacBook의 Voice Memos를 무조건 보낼 project
#   MANUAL_AUDIO_DIR         — 폰 녹음 수동 import inbox
#   NOTION_TARGET_PROJECT    — (선택) Notion 전사가 들어갈 프로젝트
#   NOTION_TOKEN             — (선택) Notion API 토큰
#   NOTION_MEETING_DBS       — (선택) Notion DB ID 목록
#   NOTION_UPLOAD_DATABASE_ID — (선택) 생성된 Markdown 노트를 업로드할 Notion DB
#   NOTION_SPACE_ID          — (선택, build_roster.sh용)
#   ROSTER_EMAIL_DOMAIN      — (선택, build_roster.sh용)
#   REMOTE_HOST              — (선택) run-remote.sh를 쓸 때만 필요한 SSH alias
#   MEETING_NOTES_SKILL      — 회의록 작성 SKILL.md 경로
#   CODEX_MODEL              — (선택) Codex --model 값

# launchd로 Voice Memos 자동 감지 트리거:
./sh/local-launchd.sh install
./sh/local-launchd.sh status
# /bin/bash, /usr/bin/find에 Full Disk Access 권한 부여 필요
```

### Codex LLM 설정

전사문 제한 교정과 회의록 생성은 `sh/run-note-llm.sh`를 통해 Codex CLI가 담당한다. 공유 가능한 설정은 `.env`에 둔다.

| 변수 | 기본/예시 | 의미 |
|---|---|---|
| `MEETING_NOTES_SKILL` | `skills/meeting-minutes/SKILL.md` | 회의록 작성 규칙의 source of truth |
| `MEETING_PREVIOUS_NOTES_LIMIT` | `3` | 같은 project의 최근 회의록 몇 개를 후속 액션 판단에 주입할지 |
| `MEETING_PREVIOUS_NOTE_MAX_LINES` | `160` | 이전 회의록 1개당 발췌 최대 줄 수 |
| `MEETING_TRANSCRIPT_CORRECTION` | `1` | 근거 기반 lexical transcript 교정 단계 활성화. `0`이면 원본을 바로 회의록 입력으로 사용 |
| `MEETING_TRANSCRIPT_CORRECTION_MIN_CONFIDENCE` | `0.92` | 이 값 미만의 LLM 제안은 자동 거절 |
| `MEETING_TRANSCRIPT_CORRECTION_PERSON_LEDGER_MIN_COUNT` | `5` | 누적 사전 인물 매핑을 자동 적용하기 위한 최소 과거 확정 횟수 |
| `MEETING_TRANSCRIPT_CORRECTION_MAX_EDIT_RATIO` | `0.05` | 교정본에서 허용하는 원본 대비 최대 변경 비율 |
| `MEETING_FACT_CHECK` | `1` | 회의록 하단 객관 명제 팩트체크 단계 활성화 |
| `MEETING_FACT_CHECK_WEB_SEARCH` | `1` | 개인정보를 제거한 공개 명제의 Codex web search 활성화 |
| `MEETING_FACT_CHECK_REASONING_EFFORT` | `high` | 객관 명제 추출·근거 판정 reasoning effort |
| `CODEX_BIN` | PATH의 `codex` | Codex CLI 경로 override |
| `CODEX_MODEL` | `frontier` | `frontier`는 실행 시점의 `OMX_DEFAULT_FRONTIER_MODEL`, 없으면 `~/.codex/config.toml`의 `model`로 해석된다 |
| `CODEX_REASONING_EFFORT` | `highest` | `highest`는 Codex `model_reasoning_effort="xhigh"`로 해석된다 |
| `CODEX_SEARCH` | `1` | Codex web search 활성화 여부 |
| `CODEX_SANDBOX` | `read-only` | Codex가 실행될 sandbox |
| `CODEX_APPROVAL_POLICY` | `never` | 비대화형 실행 중 사용자 승인 요청 금지 |

현재 이 MacBook에서는 `.env`의 `CODEX_MODEL=frontier`, `CODEX_REASONING_EFFORT=highest`를 사용한다. 스크립트가 실행 시점의 `OMX_DEFAULT_FRONTIER_MODEL` 또는 `~/.codex/config.toml` 기본 모델을 읽고, effort는 `xhigh`로 넘긴다.
- 최고 모델/effort는 품질 우선 설정이라 단일 짧은 테스트 transcript도 수 분 걸릴 수 있다. 자동 실행은 lock으로 중복 실행을 막지만, 회의 직후 산출 지연은 감수해야 한다.

단일 회의록 생성:

```bash
./sh/make-notes.sh --force --only "worxphere/20260528 150348"
```

`make-notes.sh`는 현재 회의보다 파일명 시각이 앞선 회의록만 이전 회의 컨텍스트로 사용한다. 재실행 시 나중 날짜 회의가 과거 컨텍스트로 역유입되지 않으며, 이전 참석자/화자 매핑은 현재 회의에 승계하지 않는다.

### Codex CLI 확인

```bash
command -v codex
codex --version
printf 'OK만 출력해' | codex --ask-for-approval never --sandbox read-only \
  exec --ephemeral --ignore-rules --skip-git-repo-check \
  -C "$PWD" --color never -
```

성공 기준은 실행 파일 경로와 version이 출력되고, 마지막 명령이 `OK`를 출력하는 것이다. `run-note-llm.sh`는 Codex 호출 시 `--output-last-message`를 사용해 Codex 실행 로그가 회의록 파일에 섞이지 않게 한다.

### 선택 경로: 리모트 컴퓨트 머신

현재 자동 플로우는 로컬 `run-local-pipeline.sh`다. 아래 설정은 `run-remote.sh`로 별도 원격 Apple Silicon Mac에서 처리할 때만 필요하다.

```bash
./sh/setup.sh   # Homebrew python@3.11, ffmpeg, venv, mlx-whisper, pyannote 설치
# 별도: HuggingFace에서 pyannote gated 모델 약관을 계정별 1회 수락
#   https://huggingface.co/pyannote/segmentation-3.0
#   https://huggingface.co/pyannote/speaker-diarization-community-1
# 별도: Codex CLI 설치
```

### Notion 스페이스 ID 확인 (build_roster.sh용)
```bash
sqlite3 ~/Library/Application\ Support/Notion/notion.db "SELECT id, name FROM space"
```

## 사용

| 작업 | 명령어 | 빈도 |
|---|---|---|
| Voice Memos 로컬 dry-run | `./sh/run-local-pipeline.sh --dry-run` | 자동화 설정 전/후 점검 |
| 폰 녹음 수동 import dry-run | `./sh/import-manual-audio.sh --dry-run` | 복사 파일 처리 전 점검 |
| Voice Memos 로컬 처리 | `./sh/run-local-pipeline.sh` | 수동 트리거 |
| 기존 회의록 재생성 | `./sh/run-local-pipeline.sh --force-notes --only "worxphere/YYYYMMDD HHMMSS"` | 직원명단/스킬 개선 후 재처리 |
| 회의록 Codex skill 설치 | `./sh/install-meeting-skill.sh` | 최초 1회 또는 스킬 수정 후 |
| Voice Memos 자동 처리 설치 | `./sh/local-launchd.sh install` | 최초 1회 |
| Voice Memos 자동 처리 상태 | `./sh/local-launchd.sh status` | 점검 |
| Voice Memos 자동 처리 수동 트리거 | `./sh/local-launchd.sh kickstart` | 권한/동작 확인 |
| Notion 전사 임포트 (API) | `./sh/import-notion-api.sh [YYYY-MM-DD]` | 필요 시 |
| Notion 전사 임포트 (notion.db) | `./sh/import-notion.sh [YYYY-MM-DD]` | 필요 시 (대안 경로) |
| 원격 컴퓨트 선택 실행 | `./sh/run-remote.sh` | 현재 자동 플로우 아님 |
| 직원명단 roster 갱신 | `./sh/build_employee_roster.sh` | 파이프라인 시작/노트 생성 완료 후 자동, 필요 시 수동 |
| 멤버 명부 갱신 | `./sh/build_roster.sh` | 월 1회 |

`import-notion-api.sh`/`import-notion.sh`의 선택 인자는 `--since` 날짜. 생략 시 전체 임포트.

### Voice Memos 자동화

로컬 자동화 entrypoint는 `run-local-pipeline.sh`이다. 이 경로는 Voice Memos sync → local transcription → 제한 교정 파생본 → 에이전트용 Markdown note → 일자별 사람 검토용 Notion-readable 결과 → meeting-context-reviewer 산출물까지 수행한다. Notion 업로드와 Git push는 실행하지 않는다.

기존 정본을 보존한 전체 재작성은 `rebuild-notes-to-candidate.sh`를 사용한다. 출력은 `meeting-note-rebuilds/active/<batch-date>/`에만 생성되며, 중단·비교 배치는 `meeting-note-rebuilds/archive/`에 보존한다.

`transcribe.sh`는 Whisper/pyannote 결과를 원본 `transcripts/<project>/*.txt`로 남긴다. 이어서 `correct-transcripts.sh`가 검색 도구와 이전 회의 본문 없이 현재 참석자, 직원명단, 누적 확정 사전만 사용해 lexical JSON patch를 제안받는다. 누적 사전도 같은 project에서 현재 회의보다 파일명 시각이 앞선 회의만으로 새로 만들어, 재실행 시 현재/미래 회의의 판단이 역유입되거나 자기 확정되는 것을 막는다. `apply_transcript_corrections.py`는 타임스탬프·화자·줄 순서 보존, 정확한 원문 부분 일치, 사전/명부 근거, 신뢰도, 숫자·부정어·기한 보호, 줄별/전체 변경량 제한을 검사한다. 통과한 patch만 `state/corrected-transcripts/`에 적용하고 모든 거절 사유를 manifest에 남긴다. `make-notes.sh`는 원본/교정본 SHA-256 검증이 성공한 경우에만 교정본을 사용하며, 실패하거나 기능이 꺼져 있으면 원본으로 fail-open 한다.

교정 단계는 문장 다듬기나 요약을 하지 않는다. 긴 훼손 구간, 일반 문법, 조사, 반복 발화는 그대로 유지하고 인명·제품명·조직명·약어의 최소 문자열만 다룬다. 원본 transcript는 감사와 재처리를 위해 항상 유지된다.

회의록 작성 스킬은 결론·액션·미결·근거·검증을 서로 다른 단일 원본으로 분리한다. `Action Items`를 액션의 단일 원본으로 사용하고, 별도 `Task Handoff`는 만들지 않는다.
Codex에서 직접 이 스킬을 호출할 수 있게 하려면 `./sh/install-meeting-skill.sh`를 실행한다. 설치 대상은 기본적으로 `~/.codex/skills/worxphere-meeting-minutes`이고, 파이프라인은 repo 안의 같은 `SKILL.md`를 source of truth로 읽는다.

최초 초안은 `skills/meeting-minutes/references/korean-naturalness.md`를 함께 적용한다. 이 규칙은 `humanize-korean` quick rules의 회의록용 보수적 적용본으로, 구조화 불릿·상태 라벨·검증 원문은 유지하면서 반복 종결, 번역투, 반복 영문 병기, 추상 명사화를 줄인다. `validate_meeting_note.py`가 자연스러움 위반을 구조·사실 계약과 함께 검사하며, 실패하면 기존 1회 재작성 루프가 해당 오류를 고친다.

- `핵심 결론 및 결정사항` — 최대 7개 항목, 1,400자 이내, 상세 근거는 `§4.x`로 참조
- `Action Items` — 체크박스형 결과물 문장 + `담당 범위 · 기한 · 근거 섹션` 한 줄
- `미결 쟁점 및 다음 결정` — 결정이 필요한 항목만 담당 범위·기한·근거와 함께 표시
- `상세 논의와 근거` — 결론을 재선언하지 않고 발언·맥락만 유지
- `참석자·용어 검증 부록` — 참석자, 검증 완료, 검증 필요를 하위 항목으로 유지

```bash
./sh/run-local-pipeline.sh --dry-run
./sh/run-local-pipeline.sh
./sh/run-local-pipeline.sh --force-notes --only "worxphere/20260528 150348"
./sh/correct-transcripts.sh --force --only "worxphere/20260528 150348"
./sh/make-notes.sh --force --only "worxphere/20260528 150348"
```

기존 회의록을 다시 만들 때는 `--force-notes`를 사용한다. 기존 `.md`는 덮어쓰기 전에 `state/note-backups/<project>/<timestamp>/` 아래로 백업된다. `--only`는 `NAME`, `PROJECT/NAME`, `NAME.md`, `PROJECT/NAME.md` 형식을 받는다.

launchd로 켜려면 관리 스크립트를 사용한다. `install`은 현재 Voice Memos 파일을 seen baseline으로 먼저 기록하므로, 자동화 활성화 직후 과거 녹음 전체가 한꺼번에 처리되지 않는다. macOS 권한 정책 때문에 `/bin/bash` 또는 사용하는 터미널 앱에 Full Disk Access가 필요할 수 있다.

```bash
./sh/local-launchd.sh install
./sh/local-launchd.sh status
./sh/local-launchd.sh kickstart
./sh/local-launchd.sh uninstall
```

권한이 부족하면 `logs/local-pipeline.log`에 `Voice Memos folder cannot be listed` 또는 `Operation not permitted`가 남는다. 이 경우 macOS System Settings → Privacy & Security → Full Disk Access에서 `/bin/bash`와 `/usr/bin/find`를 허용한 뒤 `./sh/local-launchd.sh kickstart`로 다시 확인한다.

파일명은 공백 없이 생성한다. Voice Memo 원본이나 수동 import 파일명에 공백이 있으면 파이프라인 입력 시 `_`로 정규화한다. 예: `20260609 140803 PD X Nika 미팅.m4a` → `20260609_140803_PD_X_Nika_미팅.m4a`.

녹음 직후 파일이 아직 쓰이는 중일 수 있어 `sync-voice-memos.sh`는 기본 60초보다 어린 `.m4a` 파일을 건너뛴다. 필요하면 `.env`에서 `VOICE_MEMO_MIN_AGE_SECONDS`로 조정한다. 현재 운영값은 `VOICE_MEMO_FORCE_PROJECT=worxphere`라서 Voice Memos 제목은 라우팅에 영향을 주지 않지만, 제목 자체는 `state/voice-memo-titles/`에 보존한다. 제목을 `정승호, 구석모 미팅` 또는 `참석자: 정승호, 구석모`처럼 작성하면 현재 회의 화자 판정의 보조 근거가 된다. 날짜·시간·장소 제목은 참석자 근거로 사용하지 않는다.

Notion 업로드 시에도 같은 제목 메타데이터와 `state/meeting-attendees/`의 확정 참석자를 사용해 `참여자` 속성을 채운다. 이미 업로드된 페이지의 빈 참여자 속성은 `upload-notion-notes.sh --refresh-participants`로 본문을 다시 쓰지 않고 보정할 수 있다.

참석자가 확정된 회의를 재처리할 때는 전용 메타데이터를 먼저 저장한다. 이 값은 직전 회의의 참석자·화자 매핑보다 우선한다.

```bash
./sh/set-meeting-attendees.sh \
  --meeting "worxphere/20260715_160933" \
  --attendees "구석모, 고병삼, 정승호"
./sh/run-local-pipeline.sh --force-notes --only "worxphere/20260715_160933"
```

폰으로 녹음한 파일을 Mac으로 복사하는 예외 상황에서는 `manual-audio/worxphere/` 아래에 `.m4a` 파일을 넣는다. 다음 로컬 파이프라인 실행 때 `import-manual-audio.sh`가 이 파일을 `audio/worxphere/`로 이동시킨 뒤 기존 전사/회의록 생성 흐름에 태운다.

앞/뒤 비발화 trim 기준은 `AUDIO_PREP_TRIM_OUTER_SILENCE`, `AUDIO_PREP_TRIM_THRESHOLD`, `AUDIO_PREP_TRIM_SILENCE_DURATION`, `AUDIO_PREP_TRIM_PADDING_SECONDS`, `AUDIO_PREP_MIN_TRIM_SECONDS`로 조정한다. 중단 후 재녹음 merge 기준은 `.env`의 `AUDIO_PREP_MERGE_GAP_SECONDS`로 조정한다. 기본값은 180초다. 잡음/무발화 필터 기준은 `AUDIO_PREP_MIN_DURATION_SECONDS`, `AUDIO_PREP_REJECT_SILENCE_RATIO`, `AUDIO_PREP_SILENCE_THRESHOLD`, `MEETING_MIN_TRANSCRIPT_CHARS`로 조정한다.

### 직원명단 기반 이름 정규화

`build_employee_roster.sh`는 Worxphere 포털의 FamilyBab 직원 디렉토리 산출물에서 회의 처리용 identity roster를 만든다. 목적은 현재 참석자 명단을 만드는 것이 아니라, 다음 전사·회의록에서 인명과 소속을 정확히 식별하면서 과거 회의에 등장한 퇴사자도 잃지 않는 것이다. 기본 입력은 `~/project/worxphere-internal/packages/portal-to-notion/data/familybab/index.md`이며, 원천이 36시간보다 오래되면 `EMPLOYEE_DIRECTORY_REMOTE_HOST`(기본 `macmini`)의 최신 산출물을 최대 1시간에 한 번 확인한다. 맥미니가 닿지 않거나 원격 파일도 오래됐으면 맥북에서 기존 `familybab_sync.sh collect`를 실행한다. 이 fallback은 포털 조회와 로컬 스냅샷 생성만 하며 Notion deploy는 실행하지 않는다.

출력은 `glossary/employee_roster.tsv`이고 `이름 / 소속팀 / 직책 / 재직상태 / 마지막 재직 확인일 / 출처`만 포함한다. 내부 병합은 정규화한 회사 이메일을 기본 식별자로 사용하며 `@jobkorea.co.kr`와 `@worxphere.ai`의 동일 local-part를 같은 사람으로 연결한다. 이메일이 없거나 별칭이 다를 때만 동명이인이 없는 이름을 보조 조건으로 사용한다. 원문 이메일, 전화번호, 사번과 내부 identity key는 glossary에 내보내지 않는다. 신선한 FamilyBab 스냅샷에 있으면 `active`, 이전 스냅샷에는 있었지만 최신 스냅샷에서 사라지면 삭제하지 않고 `former`, WDC 사용자 목록에만 있거나 원천이 오래되면 `unverified`로 구분한다. WDC 목록만으로 퇴사자를 재직자로 되돌리지 않는다.

상태 이력은 `state/employee-roster/history.json`, 마지막 점검 근거는 `state/employee-roster/status.json`에 남는다. `source_origin`, `remote_refresh_result`, `local_fallback_result`로 실제 사용 원천과 fallback 결과를 구분할 수 있다. 최초 원장 생성 시에는 더 오래된 로컬 FamilyBab 스냅샷을 역사 자료로 먼저 읽고 최신 스냅샷에서 사라진 사람을 `former`로 복원한다. 원격·맥북 수집이 모두 실패하면 기존 상태를 보존하며 누구도 새로 퇴사 처리하지 않는다. 내용이 같으면 roster·용어집·누적 표기 사전 파일을 다시 쓰지 않아 수정 시각이 실제 의미 변경을 나타내게 한다.

맥북 fallback이 실제 수집하려면 `~/project/worxphere-internal/packages/portal-to-notion/.local/session.json`이 유효해야 한다. 세션이 만료되면 수집은 실패 안전하게 종료되고 `state/employee-roster/local-fallback.log`에 원인을 남긴다. 브라우저 세션을 이 파일로 갱신하는 작업은 별도의 자격증명 처리이므로 자동 수행하지 않는다.

```bash
./sh/build_employee_roster.sh --dry-run
./sh/build_employee_roster.sh
```

`make-notes.sh`는 이 roster를 회의록 작성 스킬 입력으로 넣어 인물명과 액션아이템 담당자를 보정한다. `run-local-pipeline.sh`와 `run-remote.sh`는 노트 생성 전에 roster를 먼저 갱신한다.

## 선택 경로: Notion DB 업로드

현재 로컬 Voice Memos 자동화는 Notion 업로드를 하지 않는다. Notion write는 매번 별도 승인이 필요하다.

현재 로컬 경로의 게시 검토·승인·결과 검증은 [독립 게시 파이프라인](docs/NOTION_PUBLICATION_PIPELINE.md)을 따른다. 아래 설정은 기존 선택적 원격 경로의 설명이며 새 로컬 경로의 승인 절차를 대체하지 않는다.

`NOTION_UPLOAD_DATABASE_ID`가 설정되어 있으면 `run-remote.sh`가 실행 전후의 `notes/<project>/*.md` 목록을 비교해 이번 실행에서 새로 생성된 회의록만 Notion DB에 업로드합니다. 기존 파일은 백필하지 않습니다. 업로드 대상은 `state/notion-upload/pending.txt`에 큐잉되고, 성공하거나 DB에 같은 제목이 이미 있으면 큐에서 제거됩니다. 실패한 항목은 큐에 남아 다음 `run-pipeline.sh` 또는 `run-remote.sh` 실행 때 재시도됩니다. DB ID가 비어 있으면 새 노트를 큐에 넣지 않고 업로드 단계를 건너뜁니다.

업로드는 [`notion-native-toolkit`](https://github.com/seokmogu/notion-native-toolkit) 프로필을 사용합니다.

```bash
NOTION_NATIVE_PROFILE=                  # blank = notion-native-toolkit default profile
NOTION_NATIVE_TOOLKIT_DIR=$HOME/project/notion-native-toolkit
NOTION_UPLOAD_DATABASE_ID=00000000000000000000000000000000
```

## 노트 출력 형식

```
# {회의 주제 제목}
## 1. 핵심 결론 및 결정사항
## 2. Action Items
## 3. 미결 쟁점 및 다음 결정
## 4. 상세 논의와 근거
## 5. 참석자·용어 검증 부록
### 5.1 참석자/언급 인물
### 5.2 검증 완료
### 5.3 검증 필요
```

화자 분리된 로컬 전사는 A/B 역할 추론 섹션이 추가됨. Notion 전사는 화자 없이 평문.

## 테스트와 평가

실제 녹음, 모델 호출, Notion 연결 없이 기존 단위 테스트와 합성 실패·복구 테스트를 실행한다. 선택적 legacy uploader 테스트는 `upload-notion-notes.sh`와 같은 기존 toolkit 실행 환경의 `httpx`가 필요하다.

```bash
../notion-native-toolkit/.venv/bin/python -m unittest discover -s tests -v
for script in sh/*.sh; do bash -n "$script" || exit; done
.venv-diar-test/bin/python -m unittest discover -s experiments/speaker-assignment -p 'test_*.py' -v
git diff --check
```

기본 Python에서 `ModuleNotFoundError: httpx`가 나면 테스트 실패 원인을 런타임 의존성과 구분한다. 오디오·전사 품질과 실제 LLM/리뷰/Notion E2E는 위 합성 테스트로 입증되지 않는다. [2026-09-07 평가·개선 기록](docs/PROJECT_ASSESSMENT_2026-09-07.md)에 검증 범위와 남은 과제를 정리했다.

현재 산출물과 대기 작업은 읽기 전용 진단으로 확인할 수 있다. 요약에는 회의명·본문·인명·파일 경로를 넣지 않고, 복구 대상 경로는 로컬 계획 파일에만 기록한다. 이 명령은 복구를 실행하거나 큐를 변경하지 않는다.

```bash
assessment_dir="state/assessments/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$assessment_dir"
PYTHONDONTWRITEBYTECODE=1 python3 sh/audit_pipeline_state.py --base . \
  --output "$assessment_dir/audit.json" \
  --private-plan "$assessment_dir/recovery-plan.json"
```

진단 종료 코드 0은 발견된 결함 없음, 1은 개선 대상 있음, 2는 입력·상태 해석 오류다. 보고서 파일은 새 경로만 허용한다. 계획에는 전사 복원, 회의록 재생성, 후속 리뷰 복구를 구분해 표시하며, 적용 전 해당 회의의 오류를 확인한다.

[정답 기반 전사 평가](docs/TRANSCRIPT_QUALITY.md)는 사람이 검수한 전사를 명시적으로 입력받아 CER/WER과 지정한 중요 표현·화자 라벨을 비교한다. 이 검증은 모델이나 클라우드를 호출하지 않는다. 정답·검수·허용 기준이 없는 경우를 통과로 바꾸지 않는다. 실제 연결 점검과 운영 진단 결과는 [2차 개선 기록](docs/PHASE2_IMPROVEMENT_2026-09-07.md)에 정리한다.

## 개발 메모

- 오디오·전사·노트·glossary·로그는 `.gitignore`로 전부 제외 (프라이버시)
- `notion.db`는 Notion 데스크톱 앱의 로컬 캐시로 내부 구현 디테일. 스키마가 앱 업데이트로 바뀔 수 있음
- 로컬 경로는 완료된 전사를 재사용하고 미완료 회의록·리뷰 작업을 재시도한다. 선택적 원격 경로는 별도 구현이며 같은 복구 보장을 검증하지 않았다.
- 화자 분리는 `pyannote/speaker-diarization-community-1`의 exclusive diarization을 사용. `pyannote.audio` 4.x가 필요하므로 `mlx-whisper` venv와 분리된 `.venv-diar-test`에서 실행
- LLM 호출은 `sh/run-note-llm.sh`가 담당하며, Codex를 `codex --ask-for-approval never --sandbox read-only exec ...` 형태로 비대화형 실행한다.
