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
  -> source/transcript/readable/review hash readiness receipt
  -> state/notion-publication/pending.txt

separate publication worker
  -> validate the existing reviewed artifact hashes and lossless projection
  -> privacy and attendee metadata parsing
  -> prepare one exact request for human review
  -> require a matching unexpired approval file for live publication
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
# Validate completed artifacts and prepare a local request
./sh/run-notion-publication-pipeline.sh --prepare --limit 1

# Read-only Notion target/schema check through the Codex Notion MCP
./sh/run-notion-publication-pipeline.sh --preflight --limit 1

# Read-only preflight for explicitly public notes only
./sh/run-notion-publication-pipeline.sh --preflight --visibility public --limit 1

# After explicit approval, publish exactly that queued note
./sh/run-notion-publication-pipeline.sh --publish --limit 1 \
  --only 'notes/worxphere/<stem>.md' \
  --approval-file /path/to/approved-request.json

# Manage local preparation (new installations use --prepare)
./sh/notion-publication-launchd.sh install
./sh/notion-publication-launchd.sh status
./sh/notion-publication-launchd.sh kickstart
```

Publication state is stored under `state/notion-publication/`. A failed
projection check, preflight, Notion write, or post-write payload comparison
keeps the item in the queue. The legacy `내부미팅` DB is not a queue and is
never cleaned by this worker.

`enqueue` requires a canonical note under `notes/<project>/`, a nonempty matching
transcript, a valid canonical note, an existing faithful readable projection,
three nonempty reviewer outputs, and a matching readiness receipt. The local
completion runner writes `readiness/*.json` only after a fresh review; hashes
bind the source, transcript, readable body and review artifacts. Existing queue
items created before this contract may fail preparation. Repair only an intended
meeting with `run-local-pipeline.sh --only '<project>/<stem>'`; old notes are not
backfilled automatically. `--force-notes` also regenerates the canonical note.

## Approval and result verification

Queueing, preparation and preflight never grant publication authority. After the
user approves the exact Notion target and content in the current conversation,
the approving operator records a JSON approval with these fields:

| Field | Value |
|---|---|
| `approval_id` | Unique identifier for the explicit approval |
| `request_path`, `request_sha256` | Absolute prepared request path and its SHA-256 |
| `source_path`, `source_sha256` | Exact canonical note and hash from that request |
| `readable_path`, `readable_sha256` | Exact reviewed derivative and hash |
| `target_data_source_id` | Exact approved target from the request |
| `expires_at` | Short-lived ISO-8601 expiry with timezone |

The pipeline does not create an approval automatically. The file is a local
scope/snapshot check, not proof of who granted authorization; it must only be
created following the actual conversation approval. Both publication entrypoints
reject missing, expired or mismatched approval before invoking the Notion agent.
Live calls accept only `--limit 1`.

After the agent returns, target, visibility and title must match the prepared
request, the source/readable hashes must still match, and the freshly fetched body
must pass payload validation. A mismatch retains the queue item and reports an
error. Source changes during a remote call may mean an older snapshot was already
written; failure here does not mean the remote side was unchanged. Inspect that
target and receipt before approving a retry.

The installation template now schedules `--prepare`. This code change does not
rewrite an existing LaunchAgent. Older registered `--publish` commands lack the
required approval file and are rejected; any scheduler reconfiguration is a
separate operational action.

When a Voice Memo title changes after publication, the worker requeues the
mapped note. A same-target attendee change updates only the attendee property.
A target change creates a new routed page and records the previous page as
`cleanup_required`; it never deletes the previous page automatically.
