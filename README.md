# mlx-whisper-meeting-pipeline

English | [한국어](#핵심-설계)

Distributed meeting-notes pipeline for Korean audio. It syncs macOS Voice Memos or Notion AI transcripts, runs transcription and diarization on a remote Apple Silicon compute host, generates structured Markdown notes with Claude CLI, and can optionally publish newly generated notes back into a Notion database.

## English Overview

The pipeline keeps private meeting artifacts out of git while making the processing code reusable. Each meeting project is a subdirectory under `audio/`, `transcripts/`, and `notes/`, configured by `MEETING_PROJECTS`. Voice Memo titles are routed to projects by prefix through `VOICE_MEMO_ROUTING`; unmatched recordings land in `audio/unsorted/` for manual review.

Processing is designed as an idempotent loop: sync recordings, rsync work state to a remote Mac, transcribe with `mlx-whisper`, split speakers with `pyannote`, generate Markdown with Claude CLI and WebSearch, pull results back, and optionally commit project note repositories. Past notes feed a glossary and roster so future transcripts improve over time.

## Quick Start

```bash
cp .env.example .env
# Fill in HF_TOKEN, MEETING_PROJECTS, VOICE_MEMO_ROUTING, REMOTE_HOST.

# On the remote Apple Silicon compute host:
./sh/setup.sh

# Manual run from the local recording Mac:
./sh/run-remote.sh
```

For automatic Voice Memos processing, copy `launchd/com.example.voicememo-sync.plist.example` to `~/Library/LaunchAgents/`, edit the paths, and grant Full Disk Access to `/bin/bash` so it can read the Voice Memos group container.

## Optional Notion Upload

Set `NOTION_UPLOAD_DATABASE_ID` to upload only newly generated Markdown notes to a Notion database. Existing notes are not backfilled by default. Pending uploads are stored in `state/notion-upload/pending.txt`; successful uploads and title matches are removed from the queue, while failures remain for retry.

```bash
NOTION_NATIVE_PROFILE=                  # blank = notion-native-toolkit default profile
NOTION_NATIVE_TOOLKIT_DIR=$HOME/project/notion-native-toolkit
NOTION_UPLOAD_DATABASE_ID=00000000000000000000000000000000
```

The uploader uses [`notion-native-toolkit`](https://github.com/seokmogu/notion-native-toolkit), maps common database properties such as title/date/participants/type, and updates an existing page when local state already knows its page id. New notes use the first Markdown H1 as the Notion title, so `make-notes.sh` prompts Claude to generate a specific topic title instead of a generic `# 미팅노트`.

---

한국어 회의를 자동으로 정리·축적하는 분산 파이프라인.
녹음 소스는 macOS Voice Memos 또는 Notion AI 전사, 처리는 별도의 Apple Silicon 컴퓨트 호스트에서 mlx-whisper + pyannote + Claude CLI, 결과는 마크다운 노트로 쌓입니다.

## 핵심 설계

```
  [로컬 Mac]   Voice Memos 녹음              Notion AI 전사 (notion.db / API)
      │                                              │
      │ sync-voice-memos.sh                          │ import-notion-api.sh
      ▼                                              ▼
  audio/<project>/*.m4a                       transcripts/<project>/notion_*.txt
      │                                              │
      └──────────── rsync ──────────────► [원격 Apple Silicon Mac, SSH]
                                                     │
                                    transcribe.sh (enhance for STT, raw-ish audio for diarization)
                                                     │
                                            transcripts/<project>/*.txt
                                                     │
                                    make-notes.sh (Claude CLI + WebSearch + roster)
                                                     │
                                              notes/<project>/*.md
                                                     │
                                    rsync ◄─────────┘
  extract_glossary.py (notes → 핫워드)   build_roster.sh (Notion 멤버 명부)
                  │                               │
                  └─────── glossary/ ─────────────┘
                              │
                              ▼ (다음 run 시 transcribe/make-notes에 주입)
```

`<project>`는 `.env`의 `MEETING_PROJECTS`로 정의 (공백 분리). 각 프로젝트는 `audio/`·`transcripts/`·`notes/` 아래 독립 서브디렉터리를 갖고 독립적으로 처리됩니다.

## 피드백 루프

각 회의가 쌓일수록 다음 회의 정확도가 좋아지도록 세 가지 경로로 학습 데이터가 누적됩니다:

1. **핫워드 bias** (`extract_glossary.py`)
   과거 노트의 `## 기타 메모`·`## 검증 완료`·`## 검증 필요`에서 고유명사를 뽑아 `glossary_hotwords.txt`·`glossary_prompt.txt` 생성.
   mlx-whisper의 `--initial-prompt`로 주입되어 다음 녹음 전사 시 제품명·팀명·인명 등의 오류율을 낮춤.

2. **WebSearch 워싱** (`make-notes.sh`)
   Claude CLI가 전사 오류로 의심되는 고유명사를 WebSearch로 검증 후 정정 → `## 검증 완료`에 `원문 → 정정 (근거)` 형태로 기록.

3. **이름 정규화** (`build_roster.sh` + make-notes 프롬프트)
   Notion desktop 앱의 로컬 SQLite(`notion.db`)에서 워크스페이스 멤버를 뽑아 `roster.tsv`로 저장.
   Claude가 "민수님" → `김민수_제품팀` 식으로 풀네임+팀 매칭. 동일 사람의 여러 전사 오류(`철수/철두/철식` 같은 변이)도 한 이름으로 수렴.

## 녹음 후 처리 전략

녹음 품질은 후처리로 보완하되, 전사와 화자 분리는 서로 다른 오디오를 사용합니다. 전사에는 Demucs 보컬 분리, EQ, denoise, loudness normalization을 적용한 음성 향상 WAV를 넣어 Whisper 인식률을 높입니다. 반대로 화자 분리에는 원본에 가까운 16k mono WAV를 넣어 speaker embedding이 훼손되지 않게 합니다. 실제 테스트에서 denoise/loudnorm까지 적용한 오디오를 pyannote에 넣으면 두 화자가 92%/8%로 무너졌고, 원본계열 16k mono에서는 31%/69%로 정상 분리되었습니다.

화자 분리는 `pyannote/speaker-diarization-community-1`의 exclusive diarization을 사용합니다. 이 결과를 `mlx-whisper`의 word timestamp에 매칭해 화자가 바뀌는 지점에서 transcript segment를 다시 쪼갭니다. 이후 Claude CLI가 roster, glossary, WebSearch를 이용해 이름·고유명사·전사 오류를 보정하고 최종 미팅노트를 생성합니다.

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
#   VOICE_MEMO_ROUTING       — Voice Memos 제목 prefix → project 매핑
#   NOTION_TARGET_PROJECT    — (선택) Notion 전사가 들어갈 프로젝트
#   NOTION_TOKEN             — (선택) Notion API 토큰
#   NOTION_MEETING_DBS       — (선택) Notion DB ID 목록
#   NOTION_UPLOAD_DATABASE_ID — (선택) 생성된 Markdown 노트를 업로드할 Notion DB
#   NOTION_SPACE_ID          — (선택, build_roster.sh용)
#   ROSTER_EMAIL_DOMAIN      — (선택, build_roster.sh용)
#   REMOTE_HOST              — 원격 컴퓨트 호스트 SSH alias

# launchd로 Voice Memos 자동 감지 트리거:
cp launchd/com.example.voicememo-sync.plist.example \
   ~/Library/LaunchAgents/com.<me>.voicememo-sync.plist
# plist 내 USER/경로 수정 후:
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.<me>.voicememo-sync.plist
# /bin/bash 에 Full Disk Access 권한 부여 필요 (Voice Memos group container 읽기)
```

### 리모트 (컴퓨트 머신, Apple Silicon Mac)
```bash
./sh/setup.sh   # Homebrew python@3.11, ffmpeg, venv, mlx-whisper, pyannote 설치
# 별도: HuggingFace에서 pyannote gated 모델 약관을 계정별 1회 수락
#   https://huggingface.co/pyannote/segmentation-3.0
#   https://huggingface.co/pyannote/speaker-diarization-community-1
# 별도: Claude CLI 설치 (make-notes.sh용)
#   https://docs.anthropic.com/claude-code
```

### Notion 스페이스 ID 확인 (build_roster.sh용)
```bash
sqlite3 ~/Library/Application\ Support/Notion/notion.db "SELECT id, name FROM space"
```

## 사용

| 작업 | 명령어 | 빈도 |
|---|---|---|
| Voice Memos 자동 처리 | (launchd 자동) | 녹음 발생 시 |
| Notion 전사 임포트 (API) | `./sh/import-notion-api.sh [YYYY-MM-DD]` | 필요 시 |
| Notion 전사 임포트 (notion.db) | `./sh/import-notion.sh [YYYY-MM-DD]` | 필요 시 (대안 경로) |
| 전체 파이프라인 수동 실행 | `./sh/run-remote.sh` | 수동 트리거 |
| 멤버 명부 갱신 | `./sh/build_roster.sh` | 월 1회 |

`import-notion-api.sh`/`import-notion.sh`의 선택 인자는 `--since` 날짜. 생략 시 전체 임포트.

## Notion DB 업로드

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
## 기타 메모         (인물·회사·숫자·링크)
## 검증 완료         (`원문` → **정정** (출처))
## 검증 필요         (웹검색해도 확정 못한 항목)
```

화자 분리된 로컬 전사는 A/B 역할 추론 섹션이 추가됨. Notion 전사는 화자 없이 평문.

## 개발 메모

- 오디오·전사·노트·glossary·로그는 `.gitignore`로 전부 제외 (프라이버시)
- `notion.db`는 Notion 데스크톱 앱의 로컬 캐시로 내부 구현 디테일. 스키마가 앱 업데이트로 바뀔 수 있음
- `run-remote.sh` 각 단계는 idempotent — 이미 생성된 노트/전사는 스킵
- 화자 분리는 `pyannote/speaker-diarization-community-1`의 exclusive diarization을 사용. `pyannote.audio` 4.x가 필요하므로 `mlx-whisper` venv와 분리된 `.venv-diar-test`에서 실행
- Claude CLI 호출은 `--dangerously-skip-permissions --tools "WebSearch"` 모드. 대규모 배치 시 API 비용 주의 (300건 ≈ 3~4시간)
- `make-notes.sh`는 호출 시점의 환경변수 `CLAUDE_CODE_OAUTH_TOKEN`을 그대로 사용; 환경에 없으면 호스트의 `claude-oauth print-token` 헬퍼로 자체 조달 → launchd 등 비대화형 트리거에서도 작동
