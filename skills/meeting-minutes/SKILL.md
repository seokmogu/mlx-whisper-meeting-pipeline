---
name: worxphere-meeting-minutes
description: Use to create Korean operational meeting minutes from existing STT transcripts, especially Worxphere or AX/AI meetings. If the user asks about Voice Memos/audio/downloaded .m4a, first use /Users/seokmogu/project/mlx-whisper-meeting-pipeline instead of invoking this skill directly.
---

# Worxphere Meeting Minutes

Use this skill after Korean STT transcription and before any Notion upload or meeting-context review. The output is an operational meeting record, not a short summary.

## Audio Request Guardrail

- If the user asks to find/check/process a Voice Memo, downloaded audio file, phone recording, `.m4a`, or "음성메모" and produce meeting notes, do not use this skill directly on raw audio.
- Route the work through `/Users/seokmogu/project/mlx-whisper-meeting-pipeline`:
  1. Copy external audio into `manual-audio/worxphere/` with a stable filename.
  2. Run `./sh/run-local-pipeline.sh --only worxphere/<name>` from the pipeline repo.
  3. Verify `transcripts/worxphere/<name>.txt`, `notes/worxphere/<name>.md`, and matching review artifacts.
- This skill is only the note-writing contract consumed by `sh/make-notes.sh` after a transcript exists.

## Inputs

- Transcript: Whisper/diarized lines such as `[start - end] A: ...`, or plain Notion AI transcript text.
- Optional previous meeting context: prior decisions, prior action items, unresolved issues, and next-meeting checks.
- Optional employee roster: `name<TAB>department<TAB>position`.
- Runtime provider context: whether web search is actually available.

## Core Rules

- Write only Korean Markdown. Do not add prefaces or commentary outside the Markdown document.
- Do not invent decisions, owners, dates, metrics, or external facts.
- Preserve explicit numbers, dates, amounts, deadlines, and quoted product terms.
- If a fact, owner, due date, acronym, or source is unclear, mark it as `확인 필요`.
- Prefer detailed but structured notes. A 45+ minute substantive meeting should not be compressed into a short 5-section summary unless the transcript is mostly low-content.
- Treat `AI 추정` as the default label for inferred role, classification, and context.
- Keep the original transcript out of the final note unless the user explicitly requests an appendix.

## Workflow

1. Identify the meeting title from the actual topic. Do not use generic titles like `미팅노트`.
2. Normalize obvious STT errors in people, company, product, acronym, and tool names.
3. Extract the agenda from the discussion even when no explicit agenda was spoken.
4. Separate decisions/directions from open discussion.
5. Compare against previous meeting context when provided:
   - resolved previous actions
   - continued work
   - blocked work
   - intentionally carried-over work
   - repeated issues
6. Validate action items:
   - explicit action from the meeting
   - owner or owner gap
   - due date or timing gap
   - source/evidence from the meeting
   - whether it is ready to become a task
7. Produce task handoff rows only for actions that can become durable work items.
8. Record risks and questions for the next meeting.

## Output Contract

Use this structure in order. Include a section even when it only says `해당 없음` if the absence itself is useful for meeting continuity.

```markdown
# {specific meeting title}

- 일시: {known date/time, or 확인 필요}
- 원본: {source file name or path when provided}
- 작성 방식: AI 추정

## 1. 핵심 요약
1. ...

## 2. 주요 결정 및 방향
| 구분 | 방향 | 상태 |
| --- | --- | --- |

## 3. 주요 논의
### 3.1 {topic}
- ...

## 4. Agenda Evaluation
| 아젠다 | 결과 | 근거/메모 |
| --- | --- | --- |

## 5. Previous Action Follow-up
| 이전 액션 | 이번 회의 결과 | 상태 | 다음 처리 |
| --- | --- | --- | --- |

## 6. Action Items
| No | 액션 | 담당/대상 | 기한/시점 | 검증 상태 | 근거/비고 |
| --- | --- | --- | --- | --- | --- |

## 7. Task Handoff
| Task | 담당자 | 기한 | 출처 회의 | 완료 기준 | 의존성/리스크 |
| --- | --- | --- | --- | --- | --- |

## 8. 리스크 및 확인 필요 사항
- ...

## 9. 다음 회의에서 확인할 사항
- ...

## 10. 참석자/언급 인물
- ...

## 11. 검증 완료
- `원문` -> **정정** (근거)

## 12. 검증 필요
- ...
```

## Status Labels

Agenda and previous work:

- `충족`: addressed with a clear outcome.
- `부분 충족`: discussed, but outcome or next step is incomplete.
- `미충족`: not meaningfully addressed.
- `해소`: prior issue/action was resolved.
- `진행`: work moved forward but remains open.
- `차단`: blocked by dependency, owner, policy, data, or decision.
- `이월`: intentionally carried to the next meeting.
- `확인 필요`: factual basis, owner, due date, or policy is unclear.

Action validation:

- `확인됨`: aligned with the meeting context and specific enough.
- `수정 필요`: conflicts with scope, ownership, roadmap, or policy.
- `근거 부족`: mentioned but not supported enough to become an action.
- `중복 가능`: likely overlaps with existing work or a previous action.
- `확인 필요`: depends on unclear owner, date, source, acronym, or decision.

## Person And Proper-Noun Correction

- Use the employee roster only when the match is strong and context-compatible.
- If one roster row matches, render people as `이름(소속팀, 직책)` when useful.
- If multiple people match or the role context is weak, keep the spoken form and add `검증 필요`.
- Do not output email, phone number, employee ID, or sensitive personal data.
- Use web search only when the runtime context says web search is available and a tool is actually used.
- If no actual search was performed, do not write phrases such as `검색 결과`, `웹검색 결과`, or `공개 자료 확인`.

## Action Quality Rules

- Do not assign an owner just because a person spoke near the action.
- If only a team/domain owner is known, use that team/domain and mark `담당자 확인 필요`.
- Do not invent due dates. Use `ASAP`, `이번 주`, `다음 Weekly 전`, or the spoken timing only when present.
- `Task Handoff` rows need a completion criterion. If no completion criterion can be inferred, keep the action in `Action Items` and mark the handoff gap.
- For recurring meetings, unresolved previous actions should appear in `Previous Action Follow-up` instead of being duplicated as new actions.
