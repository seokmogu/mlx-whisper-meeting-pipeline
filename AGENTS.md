# AGENTS.md - mlx-whisper-meeting-pipeline

This repository owns the local-first Voice Memos/audio to Korean meeting-notes workflow.

## Mandatory Workflow

- When the user asks to find/check a voice memo, downloaded audio, phone recording, `.m4a`, or "음성메모" and create meeting notes, run this repository's pipeline. Do not manually split/transcribe audio into `.omx/`, and do not call the meeting-minutes skill directly on raw audio.
- For audio outside this repo, copy it into `manual-audio/worxphere/` with a stable filename, then run `./sh/run-local-pipeline.sh --only worxphere/<name>` from this directory.
- For macOS Voice Memos already known to the watcher, first inspect with `./sh/run-local-pipeline.sh --dry-run` and `tail -80 logs/local-pipeline.log`.
- The expected outputs are `audio/worxphere/<name>.m4a`, `transcripts/worxphere/<name>.txt`, `notes/worxphere/<name>.md`, and matching review artifacts under `../meeting-context-reviewer/reviews/`.
- Keep Notion upload disabled unless the user explicitly approves the exact Notion write target and scope in the current conversation.

## Skill Boundary

- `skills/meeting-minutes/SKILL.md` is the meeting-note writing contract used by `sh/make-notes.sh` after STT transcripts exist.
- The installed global skill `~/.codex/skills/worxphere-meeting-minutes` should be treated as a copy of this repo-local skill, not as a replacement for the audio pipeline.
- If this skill changes, run `./sh/install-meeting-skill.sh` and verify the installed `SKILL.md` hash matches this repo-local source.

## Verification

- Do not claim completion until the target note file exists and has a specific H1, source metadata, action items, risks, and verification sections.
- Also verify the transcript exists and the pipeline process has exited. If review generation fails but the note exists, report the review failure separately.
