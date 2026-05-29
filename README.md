# mlx-whisper-meeting-pipeline

English | [한국어](#핵심-설계)

Local-first meeting-notes pipeline for Korean audio. The active production path watches macOS Voice Memos on this MacBook, treats every local recording as a Worxphere meeting, transcribes and diarizes it locally, generates structured Markdown notes with Claude Code, and creates meeting-context-reviewer artifacts. Notion upload and remote compute are optional paths, not part of the current automatic flow.

## English Overview

The pipeline keeps private meeting artifacts out of git while making the processing code reusable. Each meeting project is a subdirectory under `audio/`, `transcripts/`, and `notes/`, configured by `MEETING_PROJECTS`. This MacBook is currently configured as a Worxphere-only recorder: `VOICE_MEMO_FORCE_PROJECT=worxphere` sends every local Voice Memo to `audio/worxphere/` regardless of memo title.

The active loop is idempotent: sync completed Voice Memos, import manually copied phone recordings, trim/merge/quarantine audio, transcribe with `mlx-whisper`, split speakers with `pyannote`, generate Markdown with `claude-oauth-run` and WebSearch, then run `meeting-context-reviewer`. Past notes and the employee roster feed the next run so transcripts and person matching improve over time.

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

This is not part of the active local Voice Memos automation. If explicitly enabled for a separate run, set `NOTION_UPLOAD_DATABASE_ID` to upload only newly generated Markdown notes to a Notion database. Existing notes are not backfilled by default. Pending uploads are stored in `state/notion-upload/pending.txt`; successful uploads and title matches are removed from the queue, while failures remain for retry.

```bash
NOTION_NATIVE_PROFILE=                  # blank = notion-native-toolkit default profile
NOTION_NATIVE_TOOLKIT_DIR=$HOME/project/notion-native-toolkit
NOTION_UPLOAD_DATABASE_ID=00000000000000000000000000000000
```

The uploader uses [`notion-native-toolkit`](https://github.com/seokmogu/notion-native-toolkit), maps common database properties such as title/date/participants/type, and updates an existing page when local state already knows its page id. New notes use the first Markdown H1 as the Notion title, so `make-notes.sh` prompts Claude to generate a specific topic title instead of a generic `# 미팅노트`.

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
  make-notes.sh
      - Claude Code가 전사 원문을 읽고 고유명사/인물/액션아이템을 보정
      - WebSearch + employee_roster.tsv + glossary 사용
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
make-notes.sh
  - Claude Code + WebSearch
  - glossary + employee_roster.tsv 주입
  - 원본 transcript 파일을 덮어쓰지 않고, 보정 결과를 notes/<project>/*.md에 반영
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
| 전사 결과 | `transcripts/<project>/*.txt` | 화자 분리 반영 |
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

### 중단 후 재녹음과 잡음 처리

전사 전에 `prepare-audio-queue.py`가 각 오디오의 앞/뒤 무음 또는 저레벨 비발화 구간을 감지해 대화가 있는 구간만 남긴다. 내부의 긴 침묵은 회의 흐름일 수 있으므로 제거하지 않는다. trim 전 원본은 `state/audio-originals/<project>/`에 보관한다.

Voice Memos를 중단했다가 바로 다시 녹음하면 macOS는 별도 `.m4a` 파일을 만든다. 로컬 파이프라인은 아직 전사/회의록이 없는 오디오 중 시작 시간이 가깝고 `AUDIO_PREP_MERGE_GAP_SECONDS` 이내로 이어지는 파일을 하나의 `* merged.m4a`로 합친다. 합쳐진 원본 segment는 `state/audio-segments/<project>/`에 보관한다.

마이크를 끄지 못해 무음이나 의미 없는 녹음이 남는 경우에는 두 단계로 걸러낸다. 전사 전에는 너무 짧거나 대부분 무음인 파일을 `state/rejected-audio/<project>/`로 격리한다. 전사 후에는 의미 있는 문자 수가 `MEETING_MIN_TRANSCRIPT_CHARS`보다 적은 transcript를 잡음/무발화 후보로 보고 transcript와 대응 오디오를 `state/rejected-*` 아래로 이동한다. 완전 삭제하지 않으므로 잘못 걸러진 파일은 수동 복구할 수 있다.

## 피드백 루프

각 회의가 쌓일수록 다음 회의 정확도가 좋아지도록 세 가지 경로로 학습 데이터가 누적됩니다:

1. **핫워드 bias** (`extract_glossary.py`)
   과거 노트의 `## 기타 메모`·`## 검증 완료`·`## 검증 필요`에서 고유명사를 뽑아 `glossary_hotwords.txt`·`glossary_prompt.txt` 생성.
   mlx-whisper의 `--initial-prompt`로 주입되어 다음 녹음 전사 시 제품명·팀명·인명 등의 오류율을 낮춤.

2. **WebSearch 워싱** (`make-notes.sh`)
   Claude Code가 전사 오류로 의심되는 고유명사를 WebSearch로 검증 후 정정 → `## 검증 완료`에 `원문 → 정정 (근거)` 형태로 기록.

3. **이름 정규화** (`build_roster.sh` + make-notes 프롬프트)
   Notion desktop 앱의 로컬 SQLite(`notion.db`)에서 워크스페이스 멤버를 뽑아 `roster.tsv`로 저장.
   Claude가 "민수님" → `김민수_제품팀` 식으로 풀네임+팀 매칭. 동일 사람의 여러 전사 오류(`철수/철두/철식` 같은 변이)도 한 이름으로 수렴.

## 녹음 후 처리 전략

녹음 품질은 후처리로 보완하되, 전사와 화자 분리는 서로 다른 오디오를 사용합니다. 전사에는 Demucs 보컬 분리, EQ, denoise, loudness normalization을 적용한 음성 향상 WAV를 넣어 Whisper 인식률을 높입니다. 반대로 화자 분리에는 원본에 가까운 16k mono WAV를 넣어 speaker embedding이 훼손되지 않게 합니다. 실제 테스트에서 denoise/loudnorm까지 적용한 오디오를 pyannote에 넣으면 두 화자가 92%/8%로 무너졌고, 원본계열 16k mono에서는 31%/69%로 정상 분리되었습니다.

화자 분리는 `pyannote/speaker-diarization-community-1`의 exclusive diarization을 사용합니다. 이 결과를 `mlx-whisper`의 word timestamp에 매칭해 화자가 바뀌는 지점에서 transcript segment를 다시 쪼갭니다. 이후 Claude Code가 roster, glossary, WebSearch를 이용해 이름·고유명사·전사 오류를 보정하고 최종 미팅노트를 생성합니다.

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
#   CLAUDE_OAUTH_RUN         — (선택) claude-oauth-run 경로 override

# launchd로 Voice Memos 자동 감지 트리거:
./sh/local-launchd.sh install
./sh/local-launchd.sh status
# /bin/bash, /usr/bin/find에 Full Disk Access 권한 부여 필요
```

### Claude Code CLI 확인

`make-notes.sh`는 Claude Code를 직접 호출하지 않고 `claude-oauth-run` wrapper를 통해 비대화형으로 실행한다. 현재 이 MacBook에서는 다음 조건을 확인했다.

```bash
command -v claude-oauth-run
claude-oauth-run --version
claude-oauth print-token 2>/dev/null | wc -c
printf 'OK만 출력해' | claude-oauth-run --dangerously-skip-permissions -p --tools "" --max-budget-usd 0.50
```

성공 기준은 실행 파일 경로가 출력되고, version이 출력되고, token 길이가 0보다 크고, 마지막 명령이 `OK`를 출력하는 것이다. `make-notes.sh`는 `CLAUDE_OAUTH_RUN`이 설정돼 있으면 그 경로를 쓰고, 없으면 PATH와 `~/.local/bin/claude-oauth-run` 순서로 찾는다.

### 선택 경로: 리모트 컴퓨트 머신

현재 자동 플로우는 로컬 `run-local-pipeline.sh`다. 아래 설정은 `run-remote.sh`로 별도 원격 Apple Silicon Mac에서 처리할 때만 필요하다.

```bash
./sh/setup.sh   # Homebrew python@3.11, ffmpeg, venv, mlx-whisper, pyannote 설치
# 별도: HuggingFace에서 pyannote gated 모델 약관을 계정별 1회 수락
#   https://huggingface.co/pyannote/segmentation-3.0
#   https://huggingface.co/pyannote/speaker-diarization-community-1
# 별도: claude-oauth-run / Claude Code 설치 (make-notes.sh용)
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
| 기존 회의록 재생성 | `./sh/run-local-pipeline.sh --force-notes --only "worxphere/YYYYMMDD HHMMSS"` | 직원명단/프롬프트 개선 후 재처리 |
| Voice Memos 자동 처리 설치 | `./sh/local-launchd.sh install` | 최초 1회 |
| Voice Memos 자동 처리 상태 | `./sh/local-launchd.sh status` | 점검 |
| Voice Memos 자동 처리 수동 트리거 | `./sh/local-launchd.sh kickstart` | 권한/동작 확인 |
| Notion 전사 임포트 (API) | `./sh/import-notion-api.sh [YYYY-MM-DD]` | 필요 시 |
| Notion 전사 임포트 (notion.db) | `./sh/import-notion.sh [YYYY-MM-DD]` | 필요 시 (대안 경로) |
| 원격 컴퓨트 선택 실행 | `./sh/run-remote.sh` | 현재 자동 플로우 아님 |
| 직원명단 roster 갱신 | `./sh/build_employee_roster.sh` | 노트 생성 전 자동, 필요 시 수동 |
| 멤버 명부 갱신 | `./sh/build_roster.sh` | 월 1회 |

`import-notion-api.sh`/`import-notion.sh`의 선택 인자는 `--since` 날짜. 생략 시 전체 임포트.

### Voice Memos 자동화

로컬 자동화 entrypoint는 `run-local-pipeline.sh`이다. 이 경로는 Voice Memos sync → local transcription → Markdown note → meeting-context-reviewer 산출물까지만 수행한다. Notion 업로드와 Git push는 실행하지 않는다.

LLM 전사 보정은 `transcribe.sh`가 아니라 `make-notes.sh`에서 수행한다. `transcribe.sh`는 Whisper/pyannote 결과를 `transcripts/<project>/*.txt`로 남기고, `make-notes.sh`가 그 원본을 읽어 Claude Code, WebSearch, glossary, 직원명단으로 이름·제품명·회사명·액션아이템 담당자를 보정한다. 이 보정 결과는 `notes/<project>/*.md`에 들어가며, 원본 transcript 파일은 감사/재처리를 위해 유지한다.

```bash
./sh/run-local-pipeline.sh --dry-run
./sh/run-local-pipeline.sh
./sh/run-local-pipeline.sh --force-notes --only "worxphere/20260528 150348"
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

녹음 직후 파일이 아직 쓰이는 중일 수 있어 `sync-voice-memos.sh`는 기본 60초보다 어린 `.m4a` 파일을 건너뛴다. 필요하면 `.env`에서 `VOICE_MEMO_MIN_AGE_SECONDS`로 조정한다. 현재 운영값은 `VOICE_MEMO_FORCE_PROJECT=worxphere`라서 Voice Memos 제목은 라우팅에 영향을 주지 않는다.

폰으로 녹음한 파일을 Mac으로 복사하는 예외 상황에서는 `manual-audio/worxphere/` 아래에 `.m4a` 파일을 넣는다. 다음 로컬 파이프라인 실행 때 `import-manual-audio.sh`가 이 파일을 `audio/worxphere/`로 이동시킨 뒤 기존 전사/회의록 생성 흐름에 태운다.

앞/뒤 비발화 trim 기준은 `AUDIO_PREP_TRIM_OUTER_SILENCE`, `AUDIO_PREP_TRIM_THRESHOLD`, `AUDIO_PREP_TRIM_SILENCE_DURATION`, `AUDIO_PREP_TRIM_PADDING_SECONDS`, `AUDIO_PREP_MIN_TRIM_SECONDS`로 조정한다. 중단 후 재녹음 merge 기준은 `.env`의 `AUDIO_PREP_MERGE_GAP_SECONDS`로 조정한다. 기본값은 180초다. 잡음/무발화 필터 기준은 `AUDIO_PREP_MIN_DURATION_SECONDS`, `AUDIO_PREP_REJECT_SILENCE_RATIO`, `AUDIO_PREP_SILENCE_THRESHOLD`, `MEETING_MIN_TRANSCRIPT_CHARS`로 조정한다.

### 직원명단 기반 이름 정규화

`build_employee_roster.sh`는 Worxphere 포털의 FamilyBab 직원 디렉토리 산출물에서 회의 처리용 roster를 만든다. 기본 입력은 `~/project/worxphere-internal/packages/portal-to-notion/data/familybab/index.md`이며, 로컬에 없으면 `EMPLOYEE_DIRECTORY_REMOTE_HOST`(기본 `macmini`)의 `/Users/agent/project/worxphere-internal/packages/portal-to-notion/data/familybab/index.md`를 읽는다.

출력은 `glossary/employee_roster.tsv`이고, 회의 처리에 필요한 `이름 / 소속팀 / 직책`만 포함한다. 이메일, 전화번호, 사번은 내보내지 않는다.

```bash
./sh/build_employee_roster.sh --dry-run
./sh/build_employee_roster.sh
```

`make-notes.sh`는 이 roster를 Claude 프롬프트에 넣어 인물명과 액션아이템 담당자를 보정한다. `run-local-pipeline.sh`와 `run-remote.sh`는 노트 생성 전에 roster를 먼저 갱신한다.

## 선택 경로: Notion DB 업로드

현재 로컬 Voice Memos 자동화는 Notion 업로드를 하지 않는다. Notion write는 매번 별도 승인이 필요하다.

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
## 요약             (3~5줄)
## 주요 논의사항     (주제별)
## 결정사항
## 액션 아이템       ([ ] 담당자 — 할 일)
## 참석자/언급 인물  (직원명단으로 확실히 특정되는 경우만)
## 기타 메모         (인물·회사·숫자·링크)
## 검증 완료         (`원문` → **정정** (출처))
## 검증 필요         (웹검색해도 확정 못한 항목)
```

화자 분리된 로컬 전사는 A/B 역할 추론 섹션이 추가됨. Notion 전사는 화자 없이 평문.

## 개발 메모

- 오디오·전사·노트·glossary·로그는 `.gitignore`로 전부 제외 (프라이버시)
- `notion.db`는 Notion 데스크톱 앱의 로컬 캐시로 내부 구현 디테일. 스키마가 앱 업데이트로 바뀔 수 있음
- `run-local-pipeline.sh`와 `run-remote.sh` 각 단계는 idempotent — 이미 생성된 노트/전사는 스킵
- 화자 분리는 `pyannote/speaker-diarization-community-1`의 exclusive diarization을 사용. `pyannote.audio` 4.x가 필요하므로 `mlx-whisper` venv와 분리된 `.venv-diar-test`에서 실행
- Claude Code 호출은 `--dangerously-skip-permissions --tools "WebSearch"` 모드. 대규모 배치 시 API 비용 주의 (300건 ≈ 3~4시간)
- `make-notes.sh`는 `claude-oauth-run --dangerously-skip-permissions -p --tools WebSearch`로 Claude를 호출해 launchd 등 비대화형 트리거에서도 작동하게 한다.
