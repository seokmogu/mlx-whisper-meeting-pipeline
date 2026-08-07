#!/usr/bin/env python3
"""Validate and apply narrowly scoped LLM transcript correction patches.

The raw transcript is immutable. The LLM only proposes lexical substitutions;
this module accepts a proposal when deterministic evidence and safety checks pass,
then writes a corrected derivative plus an audit manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from phonetic_name_candidates import given_name, jamo_similarity


POLICY_VERSION = 1
LINE_RE = re.compile(
    r"^\[(?P<start>\d{2}:\d{2}:\d{2}) - (?P<end>\d{2}:\d{2}:\d{2})\] "
    r"(?P<speaker>[^:]+): (?P<text>.*)$"
)
LEDGER_RE = re.compile(
    r"^\s*[-*]\s*(.+?)\s*(?:->|→)\s*\*\*([^*]+)\*\*.*?\((\d+)회\)"
)
HANGUL_NAME_RE = re.compile(r"^[가-힣]{2,5}$")
ALLOWED_CATEGORIES = {"person_name", "proper_noun", "organization", "acronym"}
LEXICAL_SUFFIXES = (
    "님께서", "님한테", "님하고", "님에게", "님으로", "님이", "님은", "님을", "님의", "님께",
    "님", "씨", "한테", "에게", "께", "으로", "은", "는", "이", "가", "을", "를",
    "과", "와", "로", "도", "만", "의", "에", "건", "야", "라",
)
PROTECTED_PATTERNS = (
    re.compile(r"\d+(?:[.,:]\d+)*"),
    re.compile(r"(?:안|못|없|아니|않|말)"),
    re.compile(r"(?:해야|하기로|예정|담당|기한|까지|완료|ASAP)", re.IGNORECASE),
)


@dataclass(frozen=True)
class LedgerMapping:
    variant: str
    target: str
    count: int


@dataclass(frozen=True)
class PatchDecision:
    proposal: dict[str, Any]
    accepted: bool
    reason: str
    line: int | None = None
    start: int | None = None
    end: int | None = None
    evidence: str = ""


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def optional_file_digest(path: Path | None) -> dict[str, str] | None:
    if path is None or not path.is_file():
        return None
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def load_json_document(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        raw = fenced.group(1).strip()
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("patches"), list):
        raise ValueError("proposal must be a JSON object containing a patches array")
    return data


def split_variants(raw: str) -> list[str]:
    values: list[str] = []
    for piece in re.split(r"\s*(?:/|,|·)\s*", raw):
        value = piece.strip().strip("`").strip()
        value = re.sub(r"(?:님|씨)$", "", value).strip()
        if value:
            values.append(value)
    return values


def target_base(target: str) -> str:
    return re.split(r"\s*\(", target.strip(), maxsplit=1)[0].strip()


def load_ledger(path: Path | None) -> dict[str, list[LedgerMapping]]:
    mappings: dict[str, list[LedgerMapping]] = {}
    if path is None or not path.is_file():
        return mappings
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = LEDGER_RE.match(line)
        if not match:
            continue
        target = match.group(2).strip()
        count = int(match.group(3))
        for variant in split_variants(match.group(1)):
            mappings.setdefault(variant, []).append(
                LedgerMapping(variant=variant, target=target, count=count)
            )
    return mappings


def load_roster_names(path: Path | None) -> set[str]:
    names: set[str] = set()
    if path is None or not path.is_file():
        return names
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip() or line.startswith("name\t"):
            continue
        name = line.split("\t", 1)[0].strip()
        if HANGUL_NAME_RE.fullmatch(name):
            names.add(name)
    return names


def load_attendees(path: Path | None) -> set[str]:
    if path is None or not path.is_file():
        return set()
    result: set[str] = set()
    for value in re.split(r"[,/\n]", path.read_text(encoding="utf-8", errors="replace")):
        name = value.strip()
        if HANGUL_NAME_RE.fullmatch(name):
            result.add(name)
    return result


def strip_honorific(value: str) -> tuple[str, str]:
    match = re.fullmatch(r"(.+?)(님|씨)", value.strip())
    if match:
        return match.group(1).strip(), match.group(2)
    return value.strip(), ""


def protected_tokens(value: str) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(pattern.findall(value)) for pattern in PROTECTED_PATTERNS)


def parse_line_number(proposal: dict[str, Any]) -> int | None:
    raw = proposal.get("line")
    if raw is None:
        raw = proposal.get("line_id")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        match = re.fullmatch(r"L?(\d+)", raw.strip(), re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _ledger_evidence(
    before: str,
    after: str,
    category: str,
    ledger: dict[str, list[LedgerMapping]],
    roster_names: set[str],
    person_min_count: int,
) -> str:
    before_base = ""
    suffix = ""
    for variant in sorted(ledger, key=len, reverse=True):
        if before == variant:
            before_base = variant
            break
        if before.startswith(variant) and before[len(variant) :] in LEXICAL_SUFFIXES:
            before_base = variant
            suffix = before[len(variant) :]
            break
    if not before_base:
        return ""
    for mapping in ledger.get(before_base, []):
        mapped_base = target_base(mapping.target)
        is_person = mapped_base in roster_names
        if category == "person_name" and not is_person:
            continue
        if category != "person_name" and is_person:
            continue
        expected = (mapped_base if is_person else mapping.target) + suffix
        if after != expected:
            continue
        if is_person and mapping.count < person_min_count:
            continue
        return f"ledger:{before_base}->{mapping.target} ({mapping.count})"
    return ""


def _phonetic_person_evidence(
    before: str,
    after: str,
    roster_names: set[str],
    attendees: set[str],
    attendee_threshold: float,
    roster_threshold: float,
) -> str:
    before_base, before_suffix = strip_honorific(before)
    after_base, after_suffix = strip_honorific(after)
    if before_suffix and after_suffix != before_suffix:
        return ""
    if not HANGUL_NAME_RE.fullmatch(before_base) or after_base not in roster_names:
        return ""
    similarity = max(
        jamo_similarity(before_base, after_base),
        jamo_similarity(before_base, given_name(after_base)),
    )
    threshold = attendee_threshold if after_base in attendees else roster_threshold
    if similarity < threshold:
        return ""
    source = "attendee+roster" if after_base in attendees else "roster"
    return f"{source}:phonetic={similarity:.2f}"


def decide_patch(
    proposal: Any,
    raw_lines: list[str],
    ledger: dict[str, list[LedgerMapping]],
    roster_names: set[str],
    attendees: set[str],
    *,
    min_confidence: float,
    person_min_count: int,
    attendee_threshold: float,
    roster_threshold: float,
    max_patch_chars: int,
) -> PatchDecision:
    if not isinstance(proposal, dict):
        return PatchDecision({"value": proposal}, False, "patch is not an object")
    line_no = parse_line_number(proposal)
    if line_no is None or not (1 <= line_no <= len(raw_lines)):
        return PatchDecision(proposal, False, "invalid line number", line=line_no)
    match = LINE_RE.match(raw_lines[line_no - 1])
    if not match:
        return PatchDecision(proposal, False, "target line is not a transcript utterance", line=line_no)

    before = proposal.get("before")
    after = proposal.get("after")
    category = proposal.get("category")
    confidence = proposal.get("confidence")
    if not isinstance(before, str) or not isinstance(after, str) or not before or not after:
        return PatchDecision(proposal, False, "before/after must be non-empty strings", line=line_no)
    if category not in ALLOWED_CATEGORIES:
        return PatchDecision(proposal, False, "category is not auto-applicable", line=line_no)
    if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        return PatchDecision(proposal, False, "confidence must be between 0 and 1", line=line_no)
    if float(confidence) < min_confidence:
        return PatchDecision(proposal, False, "confidence below threshold", line=line_no)
    if before == after:
        return PatchDecision(proposal, False, "before and after are identical", line=line_no)
    acronym_before = before
    for suffix in LEXICAL_SUFFIXES:
        if acronym_before.endswith(suffix):
            candidate = acronym_before[: -len(suffix)]
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{1,10}", candidate):
                acronym_before = candidate
                break
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{1,10}", acronym_before) and re.search(
        rf"(?<![A-Za-z0-9]){re.escape(acronym_before)}(?![A-Za-z0-9])",
        after,
        flags=re.IGNORECASE,
    ):
        return PatchDecision(proposal, False, "already-canonical acronym expansion", line=line_no)
    if after.startswith(before + "("):
        return PatchDecision(proposal, False, "already-canonical term expansion", line=line_no)
    if any(char in before + after for char in "\r\n[]"):
        return PatchDecision(proposal, False, "patch contains structural characters", line=line_no)
    if len(before) > max_patch_chars or len(after) > max_patch_chars:
        return PatchDecision(proposal, False, "patch exceeds lexical size limit", line=line_no)
    if len(before.split()) > 4 or len(after.split()) > 4:
        return PatchDecision(proposal, False, "patch looks like sentence rewriting", line=line_no)
    if protected_tokens(before) != protected_tokens(after):
        return PatchDecision(proposal, False, "protected number/negation/commitment token changed", line=line_no)

    body = match.group("text")
    if body.count(before) != 1:
        return PatchDecision(proposal, False, "before must occur exactly once in the target utterance", line=line_no)
    start_in_body = body.index(before)
    if len(before) / max(1, len(body)) > 0.4:
        return PatchDecision(proposal, False, "patch rewrites too much of one utterance", line=line_no)

    evidence = _ledger_evidence(
        before,
        after,
        category,
        ledger,
        roster_names,
        person_min_count,
    )
    if not evidence and category == "person_name":
        evidence = _phonetic_person_evidence(
            before,
            after,
            roster_names,
            attendees,
            attendee_threshold,
            roster_threshold,
        )
    if not evidence:
        return PatchDecision(proposal, False, "no deterministic ledger/roster evidence", line=line_no)

    body_offset = match.start("text")
    start = body_offset + start_in_body
    return PatchDecision(
        proposal,
        True,
        "accepted",
        line=line_no,
        start=start,
        end=start + len(before),
        evidence=evidence,
    )


def apply_corrections(
    raw_text: str,
    proposal_data: dict[str, Any],
    ledger: dict[str, list[LedgerMapping]],
    roster_names: set[str],
    attendees: set[str],
    *,
    min_confidence: float = 0.92,
    person_min_count: int = 5,
    attendee_threshold: float = 0.72,
    roster_threshold: float = 0.86,
    max_patch_chars: int = 60,
    max_edit_ratio: float = 0.05,
) -> tuple[str, list[PatchDecision], list[PatchDecision]]:
    raw_lines = raw_text.splitlines()
    decisions = [
        decide_patch(
            proposal,
            raw_lines,
            ledger,
            roster_names,
            attendees,
            min_confidence=min_confidence,
            person_min_count=person_min_count,
            attendee_threshold=attendee_threshold,
            roster_threshold=roster_threshold,
            max_patch_chars=max_patch_chars,
        )
        for proposal in proposal_data.get("patches", [])
    ]

    accepted: list[PatchDecision] = []
    rejected: list[PatchDecision] = [decision for decision in decisions if not decision.accepted]
    occupied: dict[int, list[tuple[int, int]]] = {}
    changed_chars = 0
    for decision in decisions:
        if not decision.accepted or decision.line is None or decision.start is None or decision.end is None:
            continue
        ranges = occupied.setdefault(decision.line, [])
        if any(not (decision.end <= start or decision.start >= end) for start, end in ranges):
            rejected.append(
                PatchDecision(decision.proposal, False, "overlapping patch", line=decision.line)
            )
            continue
        next_changed = changed_chars + (decision.end - decision.start)
        if next_changed / max(1, len(raw_text)) > max_edit_ratio:
            rejected.append(
                PatchDecision(decision.proposal, False, "global edit ratio exceeded", line=decision.line)
            )
            continue
        ranges.append((decision.start, decision.end))
        changed_chars = next_changed
        accepted.append(decision)

    corrected_lines = list(raw_lines)
    by_line: dict[int, list[PatchDecision]] = {}
    for decision in accepted:
        by_line.setdefault(decision.line or 0, []).append(decision)
    for line_no, line_patches in by_line.items():
        value = corrected_lines[line_no - 1]
        for decision in sorted(line_patches, key=lambda item: item.start or 0, reverse=True):
            assert decision.start is not None and decision.end is not None
            value = value[: decision.start] + str(decision.proposal["after"]) + value[decision.end :]
        corrected_lines[line_no - 1] = value

    corrected = "\n".join(corrected_lines)
    if raw_text.endswith("\n"):
        corrected += "\n"
    return corrected, accepted, rejected


def decision_json(decision: PatchDecision) -> dict[str, Any]:
    return {
        "line": decision.line,
        "before": decision.proposal.get("before"),
        "after": decision.proposal.get("after"),
        "category": decision.proposal.get("category"),
        "confidence": decision.proposal.get("confidence"),
        "model_reason": decision.proposal.get("reason", ""),
        "validated_evidence": decision.evidence,
        "decision": decision.reason,
    }


def write_outputs(args: argparse.Namespace) -> int:
    raw_text = args.raw.read_text(encoding="utf-8")
    proposal_data = load_json_document(args.proposal)
    corrected, accepted, rejected = apply_corrections(
        raw_text,
        proposal_data,
        load_ledger(args.ledger),
        load_roster_names(args.roster),
        load_attendees(args.attendees),
        min_confidence=args.min_confidence,
        person_min_count=args.person_ledger_min_count,
        attendee_threshold=args.attendee_phonetic_threshold,
        roster_threshold=args.roster_phonetic_threshold,
        max_patch_chars=args.max_patch_chars,
        max_edit_ratio=args.max_edit_ratio,
    )
    args.corrected.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.corrected.write_text(corrected, encoding="utf-8")
    manifest = {
        "version": POLICY_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "raw_transcript": str(args.raw),
        "raw_sha256": sha256_text(raw_text),
        "corrected_sha256": sha256_text(corrected),
        "evidence_inputs": {
            "proposal": optional_file_digest(args.proposal),
            "ledger": optional_file_digest(args.ledger),
            "roster": optional_file_digest(args.roster),
            "attendees": optional_file_digest(args.attendees),
        },
        "policy": {
            "min_confidence": args.min_confidence,
            "person_ledger_min_count": args.person_ledger_min_count,
            "attendee_phonetic_threshold": args.attendee_phonetic_threshold,
            "roster_phonetic_threshold": args.roster_phonetic_threshold,
            "max_patch_chars": args.max_patch_chars,
            "max_edit_ratio": args.max_edit_ratio,
        },
        "summary": {
            "proposed": len(proposal_data.get("patches", [])),
            "accepted": len(accepted),
            "rejected": len(rejected),
        },
        "accepted": [decision_json(item) for item in accepted],
        "rejected": [decision_json(item) for item in rejected],
    }
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"transcript corrections: proposed={manifest['summary']['proposed']}, "
        f"accepted={len(accepted)}, rejected={len(rejected)}"
    )
    return 0


def verify_outputs(args: argparse.Namespace) -> int:
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        raw_text = args.raw.read_text(encoding="utf-8")
        corrected = args.corrected.read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as exc:
        if not args.quiet:
            print(f"invalid correction artifact: {exc}", file=sys.stderr)
        return 1
    valid = (
        manifest.get("version") == POLICY_VERSION
        and manifest.get("raw_sha256") == sha256_text(raw_text)
        and manifest.get("corrected_sha256") == sha256_text(corrected)
    )
    if not valid and not args.quiet:
        print("correction artifact hash/policy verification failed", file=sys.stderr)
    return 0 if valid else 1


def show_accepted(args: argparse.Namespace) -> int:
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"invalid correction manifest: {exc}", file=sys.stderr)
        return 1
    payload = {
        "version": manifest.get("version"),
        "summary": manifest.get("summary", {}),
        "accepted": manifest.get("accepted", []),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def show_relevant_ledger(args: argparse.Namespace) -> int:
    try:
        raw = args.raw.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"cannot read transcript: {exc}", file=sys.stderr)
        return 1
    mappings = load_ledger(args.ledger)
    rows: list[LedgerMapping] = []
    for variant, candidates in mappings.items():
        pattern = re.compile(rf"(?<![가-힣A-Za-z0-9]){re.escape(variant)}(?:님|씨)?")
        if pattern.search(raw):
            rows.extend(candidates)
    rows.sort(key=lambda item: (-item.count, item.target, item.variant))
    for item in rows:
        print(f"- {item.variant} -> **{item.target}** ({item.count}회)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check-proposal", help="Validate the LLM JSON envelope")
    check.add_argument("proposal", type=Path)

    apply_parser = sub.add_parser("apply", help="Validate patches and write corrected artifacts")
    apply_parser.add_argument("--raw", type=Path, required=True)
    apply_parser.add_argument("--proposal", type=Path, required=True)
    apply_parser.add_argument("--corrected", type=Path, required=True)
    apply_parser.add_argument("--manifest", type=Path, required=True)
    apply_parser.add_argument("--ledger", type=Path)
    apply_parser.add_argument("--roster", type=Path)
    apply_parser.add_argument("--attendees", type=Path)
    apply_parser.add_argument("--min-confidence", type=float, default=0.92)
    apply_parser.add_argument("--person-ledger-min-count", type=int, default=5)
    apply_parser.add_argument("--attendee-phonetic-threshold", type=float, default=0.72)
    apply_parser.add_argument("--roster-phonetic-threshold", type=float, default=0.86)
    apply_parser.add_argument("--max-patch-chars", type=int, default=60)
    apply_parser.add_argument("--max-edit-ratio", type=float, default=0.05)

    verify = sub.add_parser("verify", help="Verify corrected/raw hashes before note generation")
    verify.add_argument("--raw", type=Path, required=True)
    verify.add_argument("--corrected", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--quiet", action="store_true")

    show = sub.add_parser("show-accepted", help="Print only accepted patches for note context")
    show.add_argument("--manifest", type=Path, required=True)

    relevant = sub.add_parser("show-relevant-ledger", help="Print ledger mappings present in a transcript")
    relevant.add_argument("--raw", type=Path, required=True)
    relevant.add_argument("--ledger", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "check-proposal":
        try:
            load_json_document(args.proposal)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"invalid transcript correction proposal: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.command == "apply":
        return write_outputs(args)
    if args.command == "verify":
        return verify_outputs(args)
    if args.command == "show-accepted":
        return show_accepted(args)
    return show_relevant_ledger(args)


if __name__ == "__main__":
    raise SystemExit(main())
