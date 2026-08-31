# Notion publication pipeline

The local Voice Memos pipeline and Notion publication are independent failure
domains.

```text
Voice Memo / external audio
  -> transcript
  -> canonical Markdown meeting note
  -> notion-readable/<project>/YYYY-MM-DD/<stem>.md
     (+ optional <stem>-actions.svg and HTML embed derivative)
  -> deterministic source-to-projection validation
  -> meeting-context review
  -> state/notion-publication/pending.txt

separate publication worker
  -> refresh the reviewed local derivative when its source hash changed
  -> privacy and attendee metadata parsing
  -> target-specific Notion schema preflight
  -> create one page
  -> fresh fetch and publication-payload lossless verification
  -> publication receipt and queue removal
```

## Routing

The meeting-note H1 is the Notion `회의명`. The latest Voice Memo title, or the
external recording filename when there is no Voice Memo title, is metadata only.

- Standalone `비공개` keyword: `내부미팅 v2`
  (`collection://7483a1ab-d3cb-4b4d-b626-309ec554d7d1`).
- No `비공개` keyword: company-visible `AI Product 팀 회의록`
  (`collection://1f62af1e-16e1-4679-9485-d7c349d28559`).

`state/meeting-visibility/<project>/<meeting>.txt` is authoritative when it
contains `public` or `private`. Filename/title markers are a legacy fallback.
Use `--visibility public` to require explicit public metadata and exclude
unclassified or private notes. Publication requests default `author_name` to
`구석모_AIProduct팀` with the verified workspace user ID for 구석모;
`NOTION_MEETING_AUTHOR_NAME` and `NOTION_MEETING_AUTHOR_USER_ID` may override
the pair explicitly.

The private DB uses text `참여자`. The team DB uses person
`회의참석자` and only exact unique Notion user-name matches are written.

## Commands

```bash
# Refresh the local readable derivative and request generation
./sh/run-notion-publication-pipeline.sh --prepare --limit 1

# Read-only Notion target/schema check through the Codex Notion MCP
./sh/run-notion-publication-pipeline.sh --preflight --limit 1

# Read-only preflight for explicitly public notes only
./sh/run-notion-publication-pipeline.sh --preflight --visibility public --limit 1

# Publish one newest queued note and require a fresh fetch receipt
./sh/run-notion-publication-pipeline.sh --publish --limit 1

# Manage the independent worker
./sh/notion-publication-launchd.sh install
./sh/notion-publication-launchd.sh status
./sh/notion-publication-launchd.sh kickstart
```

Publication state is stored under `state/notion-publication/`. A failed
projection check, preflight, Notion write, or post-write payload comparison
keeps the item in the queue. The legacy `내부미팅` DB is not a queue and is
never cleaned by this worker.

When a Voice Memo title changes after publication, the worker requeues the
mapped note. A same-target attendee change updates only the attendee property.
A target change creates a new routed page and records the previous page as
`cleanup_required`; it never deletes the previous page automatically.
