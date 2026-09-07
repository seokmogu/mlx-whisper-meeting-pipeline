# 남은 검증 공백 개선 — 2026-09-07

## 목표와 범위

1차에서 추가한 로컬 안전장치의 운영 적용 여부를 변경 없이 확인하고, 실제 품질을 측정할 수 있는 평가 경로를 만든다. 새 검증 산출물은 격리된 로컬 경로에 저장한다.

- 기존 회의·큐·readiness의 누락과 불일치를 식별하는 읽기 전용 진단 및 복구 계획 도구를 구현한다.
- 사람이 검수한 정답 전사와 비교하는 정확도·중요 표현 검증 도구를 구현한다. 정답·검수·허용 기준이 없으면 각각 미평가·미검수·측정만 완료 상태로 표시한다.
- 합성 회의 입력을 실제 reviewer에 전달해 WDC와 전략 문서 조회·리뷰 출력까지 검증한다.
- 실제 Notion 두 게시 대상의 읽기 접근과 속성 타입을 점검한다. 게시·권한 변경은 하지 않는다.
- 회귀·통합 테스트를 실행하고, 측정 결과와 남은 사람 검수·외부 쓰기 검증을 구분한다.

기존 녹음·전사·노트·큐·등록 스케줄러를 수정하거나 소급 처리하지 않는다. Notion 쓰기, 배포, 커밋·푸시는 포함하지 않는다.

## 진행 계획

1. 기존 평가와 현재 코드·실험을 확인하고 재사용할 검증 함수를 찾는다.
2. 현황 진단 도구와 정답 기반 품질 평가를 독립적으로 구현·테스트한다.
3. 읽기 전용 현황 진단과 실제 reviewer/Notion 연결 점검을 실행한다.
4. 전체 테스트와 입력 보존을 확인한 뒤 결과·복구 계획·검증 한계를 기록한다.

## 결과

**2차 목표 완료: 반복 가능한 현황 진단·복구 계획과 정답 기반 평가 도구를 추가했고, 실제 reviewer 및 Notion 읽기 연결을 검증했다. 운영 자료 갱신·기존 큐 복구·실제 정확도 측정은 아직 완료되지 않았다.**

### 구현한 기능

| 파일 | 동작과 검증 경계 |
|---|---|
| `sh/audit_pipeline_state.py` | 정본·전사·파생본·리뷰·readiness·큐·작업 상태를 읽기만 한다. 요약에는 회의명·본문·인명·경로를 넣지 않는다. 대기 큐의 결함과 전체 과거 문서의 결함을 구분한다. |
| `sh/evaluate_transcript_quality.py` | manifest에 지정된 정답·전사 파일만 읽어 CER/WER, 지정한 중요 표현의 토큰 보존, 명시적으로 정렬한 화자 라벨을 비교한다. 원문 SHA-256과 수치만 보고한다. |
| `docs/TRANSCRIPT_QUALITY.md`, `docs/transcript-quality.example.json` | 평가 입력 계약과 실행 가능한 합성 예시. 예시 임계치는 운영 기준이 아니다. |
| `tests/fixtures/reviewer-smoke.md` | 실제 회의·사람·결정이 아닌 합성 입력. 실제 reviewer 연결 점검에 사용했다. |
| `tests/test_audit_pipeline_state.py`, `tests/test_evaluate_transcript_quality.py` | 총 26개 회귀 테스트 추가. 기존 124개 테스트와 함께 통과했다. |

진단은 기본적으로 stdout에만 출력한다. 파일을 지정하면 기존 파일·입력 파일 별칭을 덮어쓰지 않고, 프로젝트 내부에서는 `state/assessments/` 아래의 새 보고서만 허용한다. private plan은 복구 명령의 인자 배열과 대상을 담은 계획이며 실행하지 않는다. 전사 복원이 필요한 항목, 노트 재생성이 필요한 항목, 파생본·리뷰만 복구할 항목을 구분한다.

평가 도구는 정답 없음(`not_evaluated`), 검수 정보 없음(`not_reviewed`), 임계치 없음(`measured_not_gated`)을 통과로 바꾸지 않는다. 동일 파일·symlink·hardlink를 정답과 측정 전사로 함께 지정하는 오류, NaN/Infinity 임계치, 일상적인 `결정:` 표기를 화자명으로 지우는 오류를 차단했다. 파이프라인의 실제 `[시작 - 종료] A:` 형식은 평가에서 제외하되 발화 내용은 보존한다.

### 현재 데이터 진단

진단 전후 **909개 기존 입력 파일의 SHA-256과 수정 시각이 모두 동일**했다. 실제 녹음 파일은 읽거나 재처리하지 않았다.

- 전체 회의록 119개, 게시 대기 46개, 로컬 미완료 job 0개.
- 현재 새 readiness 계약을 충족하는 기록은 아직 없었다. 새 기능을 기존 문서에 소급 적용하지 않았으므로, 이 상태를 데이터 손실이나 내용 오류로 해석하면 안 된다.
- 게시 추적 기록 중 정본 경로 2개와 정본 밖의 과거 경로 1개가 있었다. 후자는 `PUBLICATION_REFERENCE_UNSCOPED`로 구분했다. JSON 손상으로 오인하지 않으며 자동 이동·삭제하지 않는다.

대기 46건의 항목별 진단은 다음과 같다. 한 문서에 여러 항목이 겹칠 수 있다.

| 검사 | 해당 건수 |
|---|---:|
| readiness 기록 없음 | 46 |
| 현재 회의록 형식 계약 미충족 | 30 |
| 읽기용 파생본 없음 | 38 |
| 파생본과 현재 정본 불일치 | 1 |
| 전사 누락 또는 리뷰 파일 누락 | 0 |

대기 항목을 복구 방식으로 분류하면 **16건은 파생본·리뷰 복구**, **30건은 현재 계약에 맞는 노트 재생성 검토**가 필요하다. 형식 계약 미충족은 회의의 사실이 틀렸다는 판정이 아니다. 46건을 자동 처리하지 않았다.

전체 119건 기준으로는 전사 누락 37건, 현재 형식 계약 미충족 98건, 파생본 누락 109건, 리뷰 누락 38건이다. 과거·별도 경로의 문서가 섞여 있으므로 전체 수를 현재 녹음 처리 실패 건수로 사용하지 않는다.

최종 진단 산출물:

- `state/assessments/2026-09-07-phase2/pipeline-audit-verified.json`
- `state/assessments/2026-09-07-phase2/recovery-plan-verified.json` — 로컬 경로가 포함된 비공개 계획
- `state/assessments/2026-09-07-phase2/pipeline-audit-verified-summary.json`

`verified`가 붙은 파일이 최종 결과다. 초기 진단 파일은 진단기 보완 전 기록으로 남겨두었다.

### 실제 연결 검증

**Reviewer:** 기존 `.venv/bin/meeting-context-reviewer review`를 실제 실행했다. WDC는 `mode=ro`, 전략 자료는 파일 읽기이며, 합성 입력의 회의 기억과 직원명단 조회는 제외했다. LLM이나 Notion 쓰기를 호출하지 않았다.

- `review.md`, `review.json`, `wiki-update-candidates.md`, `evidence-query-log.json` 4개 생성, exit 0.
- 조회 그룹 2개 모두 근거를 반환했다. 전략 문서 5개가 존재했고, WDC의 Notion·GitLab 자료와 전략 문서 참조가 포함됐다.
- 합성 입력·profile·전략 문서 7개 해시 불변.
- 기록: `state/assessments/2026-09-07-phase2/reviewer-integration-summary.json`.

다시 실행할 때는 새 로컬 출력 디렉터리를 사용한다. 실제 회의 노트를 선택하거나 게시 큐에 넣는 명령이 아니다.

```bash
cd ../meeting-context-reviewer
PYTHONDONTWRITEBYTECODE=1 .venv/bin/meeting-context-reviewer review \
  --profile profiles/ax-os \
  --meeting ../mlx-whisper-meeting-pipeline/tests/fixtures/reviewer-smoke.md \
  --out ../mlx-whisper-meeting-pipeline/state/assessments/<new-run>/reviewer-smoke \
  --employee-roster ../mlx-whisper-meeting-pipeline/state/assessments/<new-run>/no-roster.tsv \
  --no-meeting-memory
```

**WDC 신선도:** 실제 연결은 정상이지만 canonical 로컬 인덱스의 마지막 완료는 **2026-08-27 16:58:59 KST**였다. 기존 `inspect_wdc_live_status()`의 24시간 기준에서 Notion·Slack·GitLab 필수 소스가 모두 `SOURCE_STALE`이었다. 다른 DB를 잘못 선택한 정황은 없고, reviewer와 WDC 가이드가 동일한 canonical 인덱스를 가리킨다. `ready`는 당시 인덱스의 완성 상태이며 현재 신선도를 뜻하지 않는다.

- 기록: `state/assessments/2026-09-07-phase2/wdc-live-health.json`.
- WDC의 자체 수집·인덱스 갱신 경로를 사용한 갱신과 완료 시각·소스 신선도·실제 조회 재확인이 후속 작업이다. 이번에는 collector나 인덱스를 실행·갱신하지 않았다.

**Notion:** 연결된 Notion fetch로 두 데이터 소스의 schema를 실제 조회했다. `회의명(title)`, `회의요약(text)`, `회의일시(date)`, `회의록작성자(person)` 및 비공개 `참여자(text)`·팀 `회의참석자(person)`가 모두 코드의 기대와 일치했다. 페이지·행·권한을 변경하지 않았다.

- 기록: `state/assessments/2026-09-07-phase2/notion-schema-check.json`.
- 이는 연결된 Notion 읽기 접근과 schema 호환 증거다. headless Codex 게시 작업의 인증, 실제 생성·수정, 업로드된 본문의 fresh-fetch E2E를 증명하지 않는다.

**스케줄러:** 로컬 Voice Memos 서비스는 등록되어 있고 조회 시 미실행·마지막 exit 0이었다. 게시 plist는 존재하며 예전 `--publish` 인자를 갖지만 서비스는 로드되어 있지 않았다. 어떤 작업도 재설치·시작·종료하지 않았다.

### 품질 평가 실행과 최종 테스트

사람이 검수한 실제 정답 데이터가 제공되지 않아 **실제 회의 정확도는 미평가**다. 대신 새 CLI에 동일한 합성 전사와 숫자를 바꾼 합성 전사를 각각 넣어 기대한 분기가 실행되는지 확인했다.

- 동일한 합성 입력: `gated_pass` 1개, 정답 없는 사례 `not_evaluated` 1개, exit 0.
- 숫자를 바꾼 합성 입력: CER/WER 예시 임계치 안이어도 중요 표현 보존 실패로 `gated_fail` 1개, 정답 없는 사례 `not_evaluated` 1개, exit 1.
- 이 수치는 평가기의 동작 검증이며 실제 ASR 정확도로 사용하지 않는다.
- 기록: `state/assessments/2026-09-07-phase2/quality-example/report*.json`.

| 최종 검사 | 결과 |
|---|---|
| toolkit 실행 환경에서 `python -m unittest discover -s tests -v` | **150개 통과**, 37.387초, exit 0 |
| 기존 화자 할당 실험 테스트 | **7개 통과** |
| Python 구문 검사 | **41개 통과** |
| `bash -n` | **24개 통과** |
| `git diff --check` | **통과** |

전체 테스트 로그는 `state/assessments/2026-09-07-phase2/full-tests.log`에 있다. 초기 작업의 변경을 보존했으며 커밋·푸시하지 않았다.

### 남은 작업의 순서와 종료 기준

1. **WDC 인덱스 갱신:** 소유 프로젝트의 경로로 갱신하고 새 완료 시각·필수 소스 신선도·실제 쿼리를 확인한다.
2. **대기 46건 복구:** 로컬 계획의 16건/30건을 구분해 지정 회의부터 검토한다. 전사·정본·파생본·리뷰와 readiness 해시 검증을 통과해야 로컬 준비 완료다.
3. **실제 품질 평가:** 사람이 검수한 대표 정답·중요 표현·화자 라벨 및 허용 기준을 manifest에 제공한다. 합성 테스트를 품질 결과로 대체하지 않는다.
4. **Notion 쓰기 E2E:** 현재 대화에서 정확한 대상과 내용 승인이 있을 때 한 건의 생성/수정·fresh fetch를 검증한다. 읽기 성공만으로 게시를 시작하지 않는다.

이번 목표의 완료 기준인 진단·평가 도구 구현, 실제 읽기 연결 검증, 입력 보존 확인, 회귀 테스트와 후속 계획 기록을 충족했다. 위 운영 복구와 실제 품질 평가는 별도 미완료 항목으로 남긴다.
