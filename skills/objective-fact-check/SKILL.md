---
name: objective-fact-check
description: Check objectively decidable claims and conflicting factual statements in a diarized meeting transcript, using privacy-safe public web searches and direct source URLs while refusing to judge opinions, proposals, predictions, or unresolved internal disputes.
---

# Objective Fact Check

Create an explanatory fact-check after the structured meeting note exists. The meeting note records what participants said and decided; this check separately identifies objective propositions that conflict with deterministic rules or authoritative public facts.

## Two modes

### Extract

Read the meeting note and timestamped transcript without web search.

- Extract only atomic, falsifiable propositions that could materially affect a decision, action, risk, number, date, technical capability, definition, or rule.
- Preserve the exact speaker label and transcript start/end seconds.
- Detect pairs such as `P` and `not P` across speakers. If no objective authority can decide the pair, classify it as `speaker_conflict`; never choose a winner.
- Exclude opinions, preferences, proposals, forecasts, value judgments, rhetorical examples, and meeting-created decisions from objective verdicts.
- Mark unusual numbers, equations, or garbled language as `stt_risk` so the reader checks the audio before blaming a speaker.
- For a publicly checkable claim, produce a self-contained `public_claim` with names, private organizations, customer data, internal codenames, and private meeting context removed.
- Set `web_search_allowed=false` for internal ownership, unreleased plans, private metrics, personal claims, and anything whose search query would expose internal context.
- For every `internal_authority` candidate, name the specific authoritative record needed in `required_evidence`, such as an approved backlog, organization chart, access-control configuration, audit log, metric definition, or identity-mapping specification.
- Return no more than eight high-value candidates. Do not pad the result.

### Verify

Read only the privacy-safe candidate packet. Do not read the raw transcript or meeting note.

- Use live web search for every `public_web` or `deterministic` candidate.
- Prefer deterministic proof, standards bodies, public authorities, official product documentation, and original public datasets. Use reliable secondary sources only when no primary source exists and at least two independent sources agree.
- Link directly to the supporting page. Search-result URLs, homepages with no locator, invented URLs, and uncited model memory are not evidence.
- Write `corrected_fact`, `explanation`, and source relevance in natural Korean. Preserve an official source title or section locator in its original language when necessary.
- Record the applicable version or `valid_as_of` date. A currently true fact must not be projected backward across a version change.
- Use `contradicted` only with high confidence, a corrected fact, a concrete explanation, and adequate sources. Otherwise use `partially_verified` or `not_assessable`. Do not emit `stt_review_needed` in Verify mode; the separate audio interval review owns that gate.
- Explain the error type: definition mismatch, unit/calculation error, date/version mismatch, scope overgeneralization, correlation/causation error, outdated fact, wrong authority, or other.
- Keep any quoted source locator short. The URL and section/table/document name matter more than a long quote.

## Verdict boundary

- `verified`: authoritative evidence directly supports the proposition.
- `contradicted`: authoritative evidence directly conflicts with the proposition and explains the correction.
- `partially_verified`: only part of the proposition is supported or its scope/version differs.
- `speaker_conflict`: factual statements conflict, but no objective authority in scope decides them.
- `not_assessable`: evidence is missing, private, stale, inaccessible, or too context-dependent.
- `stt_review_needed`: the transcript is likely wrong or too ambiguous to evaluate safely.

Never label a person as wrong. Attribute the timestamp only for traceability and describe the proposition as conflicting with the evidence.

## Publication boundary

- Do not automatically rewrite decisions, actions, transcript text, or attendee attribution.
- Append the rendered result only under `## 6. 객관 명제 팩트체크` at the bottom of the meeting note.
- Every `객관 사실과 충돌` item must include the original claim, corrected fact, why it differs, a source URL or deterministic evidence, version/date scope, and confidence.
- If the web search was unavailable or unobserved, say so and do not claim external verification.
- Keep internal-authority `not_assessable` items in JSON, but summarize them compactly in the meeting note instead of expanding every private claim.
- Do not leave internal-authority items as a count only. Render the claim, why public evidence cannot decide it, and the named internal record needed for verification.
- When a publicly checked claim has `stt_risk`, re-transcribe only the referenced audio interval before the final verdict. Preserve the external sources, but use `stt_review_needed` unless the interval re-transcription supports the extracted proposition.
