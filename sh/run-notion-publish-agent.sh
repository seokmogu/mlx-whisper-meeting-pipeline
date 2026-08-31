#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${MEETING_BASE_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
REQUEST=""
RECEIPT=""
MODE=""

usage() {
  cat <<'USAGE'
Usage: run-notion-publish-agent.sh --request FILE --receipt FILE <--preflight|--publish>

Uses the configured Codex OAuth Notion MCP connection. Preflight is read-only;
publish may create one page and must re-fetch it before returning success.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --request)
      REQUEST="${2:?--request requires a file}"
      shift
      ;;
    --receipt)
      RECEIPT="${2:?--receipt requires a file}"
      shift
      ;;
    --preflight)
      MODE="preflight"
      ;;
    --publish)
      MODE="publish"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [ -z "$REQUEST" ] || [ -z "$RECEIPT" ] || [ -z "$MODE" ]; then
  usage >&2
  exit 2
fi
if [ ! -s "$REQUEST" ]; then
  echo "publication request not found: $REQUEST" >&2
  exit 1
fi

CODEX_BIN="${CODEX_BIN:-codex}"
if ! command -v "$CODEX_BIN" >/dev/null 2>&1; then
  echo "codex CLI not found: $CODEX_BIN" >&2
  exit 1
fi

schema="$SCRIPT_DIR/notion-publication-receipt.schema.json"
last_message="$(mktemp)"
log_file="$(mktemp)"
trap 'rm -f "$last_message" "$log_file"' EXIT

codex_args=(
  --ask-for-approval never
  --sandbox read-only
  exec
  --ephemeral
  --skip-git-repo-check
  --ignore-rules
  -C "$BASE"
  --color never
  -c "model_reasoning_effort=\"${NOTION_PUBLICATION_CODEX_REASONING_EFFORT:-low}\""
  --output-schema "$schema"
  -o "$last_message"
)
if [ -n "${NOTION_PUBLICATION_CODEX_MODEL:-}" ]; then
  codex_args+=(-m "$NOTION_PUBLICATION_CODEX_MODEL")
fi
codex_args+=(-)

if ! "$CODEX_BIN" "${codex_args[@]}" >"$log_file" 2>&1 <<PROMPT
You are the Notion publication worker for one completed Korean meeting note.

Mode: $MODE
Request JSON: $REQUEST

Hard boundaries:
- Read the request JSON and the local source/readable files it names.
- Use only the configured notion MCP for Notion reads and writes.
- Never modify an existing page body and never move, archive, trash, or delete an existing Notion page. The only allowed existing-page write is an attendee-property-only update on existing_publication.page_id when it is already in the requested target.
- Never modify a local file.
- Never publish unless projection_validation.faithful is true.
- Resolve request.author_name and request.author_user_id to the same exact Notion user and never substitute the connected user or another person.
- Keep message and all receipt metadata free of source text, attendee names, emails, and internal content. The fetched_markdown field is the only exception and is consumed locally for deterministic validation.
- The final response must be one JSON object matching the supplied output schema.

Target contracts:
- private target: collection://7483a1ab-d3cb-4b4d-b626-309ec554d7d1
  - 회의명: title
  - 회의일시: date
  - 회의요약: text
  - 회의록작성자: person
  - 참여자: text
  - no 공유 상태 property
- public target: collection://1f62af1e-16e1-4679-9485-d7c349d28559
  - 회의명: title
  - 회의일시: date
  - 회의요약: text
  - 회의록작성자: person
  - 회의참석자: person
  - 공유 상태: 공유됨

Required procedure:
1. Fetch the request's exact target data source and verify its schema matches the applicable contract.
2. Verify request visibility and target_data_source_id agree with the contracts above. Resolve request.author_name to exactly one Notion user, require its id to equal request.author_user_id, and hold without writing if the exact match is missing, ambiguous, or has a different id.
3. Query the target for an exact 회의명 match before any create. If an existing row is found, fetch it.
   - If it is not the exact existing_publication.page_id from the request, return status=held without writing.
   - If mode is publish and it is the exact pipeline-owned page, update only 참여자 (private) or 회의참석자 (public) when the requested attendee value differs. Never replace its body. Re-fetch it and return status=updated after verification.
   - If no attendee change is required, return status=skipped after verification.
4. If mode is preflight, perform no write and return status=preflight only after the target schema, exact author, routing, and duplicate checks pass. Return empty page_id/url/fetched_markdown and post_fetch_verified=false.
5. If mode is publish and no duplicate/conflict exists:
   - Read the readable Markdown file. Use its H1 as 회의명 but omit that leading H1 from the page body.
   - For each request.visual_assets item, read embed_html_path, create a temporary Notion HTML attachment with the Notion attachment tool, and replace that item's exact local_markdown marker with the returned renderable <embed> markdown_source. Do not publish a local file path. Hold without creating the page if any requested visual cannot be uploaded or embedded.
   - Use request.author_user_id, after the exact name/id verification above, for 회의록작성자.
   - For a public note, resolve each supplied attendee by exact unique Notion user name. Put only unique exact matches into 회의참석자; omit unresolved names.
   - For a private note, write the supplied attendee names as comma-separated text to 참여자.
   - Populate 회의일시 and 회의요약 from the request exactly.
   - Create exactly one page under the target data source.
6. Fetch the created page again. Confirm the parent data source, title, date, applicable attendee field, every required meeting-note heading, and every requested visual embed in the body. Put the exact freshly fetched page body Markdown, without the database page title or property wrapper, in fetched_markdown. Compare the fetched body with the exact publication payload after visual-asset substitution and do not claim lossless post-fetch verification if any visible source string is missing, added, changed, duplicated, or reordered.
7. Return status=created, updated, or skipped only after the fresh fetch succeeds and the checks pass. Otherwise return status=error with post_fetch_verified=false and the page id/url if available.
PROMPT
then
  cat "$log_file" >&2
  exit 1
fi

mkdir -p "$(dirname "$RECEIPT")"
cp "$last_message" "$RECEIPT"
python3 -m json.tool "$RECEIPT" >/dev/null
python3 - "$RECEIPT" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)
print(json.dumps({
    "status": value.get("status"),
    "visibility": value.get("visibility"),
    "target_data_source_id": value.get("target_data_source_id"),
    "page_id": value.get("page_id"),
    "post_fetch_verified": value.get("post_fetch_verified"),
}, ensure_ascii=False))
PY
