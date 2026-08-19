---
name: worxphere-meeting-minutes
description: Create concise, evidence-backed Korean operational meeting minutes from an existing STT transcript, especially for Worxphere or AX/AI meetings. Use when converting a transcript into a reader-first note with decisions, canonical action items, blockers, detailed discussion, attendee evidence, and verification gaps. If the request involves raw Voice Memos, audio, downloaded recordings, or .m4a files, route through /Users/seokmogu/project/mlx-whisper-meeting-pipeline before using this skill.
---

# Worxphere Meeting Minutes

Create an operational record after transcription and before any Notion upload or meeting-context review. Write a short reader layer first and preserve detailed evidence below it.

## Audio guardrail

- Route raw audio, Voice Memos, phone recordings, downloaded recordings, and `.m4a` files through `/Users/seokmogu/project/mlx-whisper-meeting-pipeline`.
- Verify the transcript, note, and matching review artifact before claiming the audio workflow is complete.
- Use this skill directly only after a transcript exists.

## Inputs

- Required transcript: diarized lines such as `[start - end] A: ...` or plain transcript text.
- Optional previous context: prior decisions, action items, unresolved issues, and next-meeting checks.
- Optional employee roster: `name<TAB>department<TAB>position`.
- Optional runtime context: whether web search is actually available.

## Non-negotiable rules

- Output only Korean Markdown. Do not add conversational prefaces or completion messages.
- Do not invent or upgrade decisions, owners, dates, metrics, attendees, or external facts.
- Preserve explicit names, numbers, dates, amounts, deadlines, negation, conditions, and quoted product terms.
- Mark an unclear fact, owner, due date, acronym, or source as `확인 필요`.
- Treat inferred roles, classifications, and context as `AI 추정`.
- Keep the raw transcript out of the note unless the user explicitly requests an appendix.
- Keep one canonical action registry in `## 2. Action Items`. Do not create a separate `Task Handoff` section.
- Do not repeat the same decision or action across the reader and evidence layers. Refer to an action by its ID when needed.
- Keep `### 5.1 참석자/언급 인물` visibly expanded. Do not hide identity evidence in a collapsed section.
- Give each topic one semantic home: conclusion, action, unresolved decision, evidence, or verification appendix. Other sections may reference its ID or `§4.x`, but must not restate it.

## Workflow

1. Identify a specific title from the actual topic.
2. Normalize only high-confidence STT errors in people, company, product, acronym, and tool names.
3. Classify each important statement before writing:
   - confirmed decision or agreed direction
   - proposal or open discussion
   - explicit action
   - blocker, risk, or verification gap
   - business-relevant numeric evidence such as baselines, targets, durations, counts, costs, and document sizes
4. Assign each classified item to exactly one home before drafting:
   - `## 1`: confirmed conclusion or decision only
   - `## 2`: explicit commitment or request only
   - `## 3`: unresolved decision, blocker, or verification gap requiring follow-up
   - `## 4`: source-grounded discussion and evidence only
   - `## 5`: attendee, lexical correction, and verification evidence only
5. Build the reader layer in sections 1–3:
   - combine summary and decisions into at most seven outcome-first bullets and 1,400 Korean characters; use fewer when the meeting has fewer independent outcomes
   - put every actionable item once in the canonical action checklist
   - merge blockers and next-meeting checks into one unresolved-decision list
6. Build the evidence layer in sections 4–5:
   - preserve substantive discussion by topic without redeclaring the conclusion or action
   - include previous-action change only when previous context exists and the state changed
   - include agenda evaluation only when an explicit agenda exists or the user requests it
   - preserve attendee and verification evidence
7. Assign contiguous `A1...An` and `M1...Mn` IDs only after final classification. Once a note is published, do not renumber its IDs.
8. Validate names, dates, amounts, decisions, action ownership, and due dates against the transcript and supplied context.
9. Confirm that every material numeric example appears in at least one evidence section even when it is omitted from the reader layer.
10. Remove exact and near-duplicate statements. Keep the semantic home and replace other occurrences with an ID or `§4.x` reference.

## Korean writing rules

- Lead with the outcome, then add the evidence or condition.
- Keep one main claim per sentence. Split a sentence when stacked clauses make it hard to scan.
- Prefer concrete subjects and verbs over abstract nominalizations.
- Avoid repeating formulaic endings such as `방향이 확인됐다`, `제시됐다`, and `논의됐다`; use the verb that matches the source status.
- Distinguish `결정했다`, `제안했다`, `검토하기로 했다`, and `확인 필요` exactly. Naturalness must not change certainty.
- Remove unnecessary commas after connective endings, but preserve punctuation inside direct quotations.
- Preserve established technical terms such as GPT, AI, GWS, Notion, Slack, Skill, MCP, API, LLM, input/output, and ROI.
- Preserve business-relevant numeric examples in the evidence layer. Do not drop a baseline, target, duration, team count, cost, or document size merely to shorten the note.
- Do not use inline code in sections 1–4. Use ordinary text or Korean quotation marks for program names, common words, durations, and numbers. Reserve inline code for literal STT fragments in the verification appendix.
- Do not run a broad second-pass rewrite over the evidence layer. If a separate language-polishing step is requested, limit it to `## 1. 핵심 결론 및 결정사항` and revalidate protected facts afterward.

## Output contract

Use exactly these five H2 sections. Sections 1–3 are the reader layer; sections 4–5 are the evidence layer.

```markdown
# {specific meeting title}

- 일시: {known date/time, or 확인 필요}
- 원본: {source file name or path when provided}
- 작성 방식: AI 추정

## 1. 핵심 결론 및 결정사항
- **{status} · {conclusion or decision}** (§4.1)

## 2. Action Items
*표기 순서: 담당 범위 · 기한 · 근거 섹션*

- [ ] **A1 · {observable result or deliverable}**
  {owner or owner scope with 담당자 확인 필요} · {spoken due timing or 기한 확인 필요} · §4.1

## 3. 미결 쟁점 및 다음 결정
- **M1 · {decision or verification needed}**
  {owner scope or 담당자 확인 필요} · {decision timing or 기한 확인 필요} · §4.1

## 4. 상세 논의와 근거
### 4.1 {topic}
- {source-grounded statement or context; do not restate sections 1–3}

### 4.x 이전 액션 변화
- **{status} · {previous action}**
  {previous state} → {current change} → {next step or A#}

### 4.x Agenda Evaluation
- **{result} · {agenda}**
  {short evidence or note}

## 5. 참석자·용어 검증 부록
### 5.1 참석자/언급 인물
- ...

### 5.2 검증 완료
- `원문` → **정정** (근거)

### 5.3 검증 필요
- ...
```

Omit the `이전 액션 변화` subsection when no previous context exists or no prior state changed. Omit the `Agenda Evaluation` subsection when no explicit agenda exists. Keep all five H2 sections and all three appendix subsections.

Do not use Markdown or Notion tables for decisions, actions, previous actions, or agenda evaluation. Use compact two-line lists. Create or link a Notion database only when the user explicitly requests database tracking and approves its target and scope.

## Status rules

- Decisions: use `확정` or `방향 합의` only when the transcript supports that certainty. Put proposals in `## 3. 미결 쟁점 및 다음 결정` or `## 4. 상세 논의와 근거`.
- Previous work: use `해소`, `진행`, `차단`, `이월`, or `확인 필요`.
- Actions: do not print a separate verification status. Put only explicit commitments or requests in the checklist. Move proposals, unsupported candidates, and decisions still needed to `## 3. 미결 쟁점 및 다음 결정`.
- Agenda: use `충족`, `부분 충족`, `미충족`, or `확인 필요`.

## Action quality rules

- Create an action only from an explicit meeting commitment or request.
- Do not assign an owner because a person spoke near the action.
- Never use anonymized speaker labels such as `화자 A`, `화자 B`, `화자 C`, or `화자 ?` as an owner. Use a source-supported person or team; otherwise write `담당자 확인 필요`.
- Use a team or domain owner with `담당자 확인 필요` when only that scope is known.
- Do not invent due dates. Preserve only spoken timing such as `이번 주` or `다음 Weekly 전`.
- Write the action as an observable result or deliverable so that a separate `완료 기준` label is unnecessary. Include an essential completion condition in the action sentence only when the meeting supports it.
- Put one compact metadata line below each checklist item in this order: owner scope, due timing, evidence-section reference. Use `담당자 확인 필요` or `기한 확인 필요` in place; do not repeat labels such as `담당:`, `기한:`, `완료 기준:`, `검증 상태:`, or `근거:` for every action.
- Do not nest inline code inside a bold checklist title. Use ordinary text or Korean quotation marks for a quoted program name so Notion does not create broken nested emphasis.
- Reference supporting discussion as `§4.1` or another `§4.x` subsection instead of copying the evidence sentence. Put a material dependency or unresolved decision in `## 3. 미결 쟁점 및 다음 결정` and reference the action ID there.
- Keep recurring unresolved work under `이전 액션 변화`; do not duplicate it as a new action unless the meeting created a new commitment. Record only `이전 상태 → 이번 변화 → 다음 단계`.
- Assign stable IDs such as `A1`, `A2`, and reference those IDs elsewhere instead of copying the action text.

## Person and proper-noun rules

- Use the employee roster only when the match is strong and context-compatible.
- Render a unique, context-compatible match as `이름(소속팀, 직책)` when useful.
- Keep the spoken form and add `검증 필요` when multiple people match or role context is weak.
- Do not output email, phone number, employee ID, or unnecessary personal data.
- Use web search only when it is available and actually used. Never claim a search that did not occur.

## Verification section rules

- Put an item in `### 5.2 검증 완료` only when the exact lexical correction is supported by transcript repetition, supplied roster, glossary, identity ledger, or an actually used authoritative source.
- Do not replace an ambiguous STT clause with a later business conclusion and call it a lexical correction. Record the confirmed conclusion in section 1 or 2, and keep the ambiguous source fragment in `### 5.3 검증 필요`.
- Never correct a numeric or date-like STT fragment in `검증 완료`. Preserve the confirmed business date elsewhere, and keep the garbled numeric fragment in `검증 필요` unless a separately verified corrected transcript already supplied the exact form.
- Do not place the same source fragment in both `검증 완료` and `검증 필요`.
- Keep `추정`, `후보`, `가능성`, `불명확`, and `확인 필요` out of `검증 완료`.

## Final quality check

- Confirm that the first non-empty line is a specific H1 title.
- Confirm that `핵심 결론 및 결정사항` has at most seven bullets and 1,400 Korean characters, without padding the section with repeated outcomes.
- Confirm that every action exists only once, is written as an observable result, and includes an ID plus one compact owner · due · evidence-reference line.
- Confirm that Action Items does not use a table or repeat per-action field labels.
- Confirm that action and unresolved-decision IDs are contiguous after final classification.
- Confirm that sections 1–4 contain no inline code and no tables.
- Confirm that each topic has one semantic home; other sections use only its ID or `§4.x` reference.
- Confirm that proposals are not presented as decisions.
- Confirm that numbers, dates, names, quoted terms, negation, and uncertainty match the source.
- Confirm that material baselines, targets, durations, counts, costs, and document sizes remain represented in the evidence layer.
- Confirm that `검증 완료` contains no numeric or date-fragment rewrites.
- Confirm that completed and pending verification items do not overlap.
- Confirm that exactly five H2 sections and all appendix subsections exist, and `Task Handoff` does not exist.
