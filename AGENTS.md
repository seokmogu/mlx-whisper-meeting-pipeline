# AGENTS.md - mlx-whisper-meeting-pipeline

This repository owns the local-first Voice Memos/audio to Korean meeting-notes workflow.

## Mandatory Workflow

- When the user asks to find/check a voice memo, downloaded audio, phone recording, `.m4a`, or "음성메모" and create meeting notes, run this repository's pipeline. Do not manually split/transcribe audio into `.omx/`, and do not call the meeting-minutes skill directly on raw audio.
- For audio outside this repo, copy it into `manual-audio/worxphere/` with a stable filename, then run `./sh/run-local-pipeline.sh --only worxphere/<name>` from this directory.
- For macOS Voice Memos already known to the watcher, first inspect with `./sh/run-local-pipeline.sh --dry-run` and `tail -80 logs/local-pipeline.log`.
- The expected outputs are `audio/worxphere/<name>.m4a`, `transcripts/worxphere/<name>.txt`, `notes/worxphere/<name>.md`, and matching review artifacts under `../meeting-context-reviewer/reviews/`.
- Keep Notion upload disabled unless the user explicitly approves the exact Notion write target and scope in the current conversation.

## Notion Publication Boundary

- The local audio pipeline may enqueue a note only after transcript, meeting note, and context review generation finish. It must not perform a Notion write itself.
- The local pipeline always renders a human-review derivative under `notion-readable/<project>/YYYY-MM-DD/<stem>.md`, plus an optional SVG only when the meeting structure benefits from it. This local result does not authorize publication.
- `sh/run-notion-publication-pipeline.sh` is the independent publication-decision entrypoint. It refreshes and validates the reviewed derivative, then delegates only an approved live write to the configured Codex Notion MCP connection.
- Route the latest Voice Memo title or external recording filename containing the standalone keyword `비공개` to private data source `collection://7483a1ab-d3cb-4b4d-b626-309ec554d7d1` (`내부미팅 v2`). Route every other completed note to company-visible data source `collection://1f62af1e-16e1-4679-9485-d7c349d28559` (`AI Product 팀 회의록`).
- Use the generated meeting-note H1 as `회의명`; the recording title or filename is only privacy and attendee metadata. Preserve `참여자` as text in the private DB and map exact unique Notion users to `회의참석자` in the team DB.
- Never seed, migrate, move, archive, trash, or delete rows from the legacy `내부미팅` database automatically. Historical migration and cleanup are separately approved batch scopes.

## Skill Boundary

- `skills/meeting-minutes/SKILL.md` is the meeting-note writing contract used by `sh/make-notes.sh` after STT transcripts exist.
- The installed global skill `~/.codex/skills/worxphere-meeting-minutes` should be treated as a copy of this repo-local skill, not as a replacement for the audio pipeline.
- If this skill changes, run `./sh/install-meeting-skill.sh` and verify the installed `SKILL.md` hash matches this repo-local source.

## Verification

- Do not claim completion until the target note file exists and has a specific H1, source metadata, action items, risks, and verification sections.
- Also verify the transcript exists and the pipeline process has exited. If review generation fails but the note exists, report the review failure separately.
