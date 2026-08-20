#!/usr/bin/env python3
"""Build a privacy-minimal, history-preserving meeting identity roster.

The roster exists to improve person-name recognition in meeting transcripts. It
is not an HR system and must not turn a stale or partial source into an employment
claim. A fresh FamilyBab/AI-subscription snapshot is authoritative for current
employment; the WDC Notion-user export only widens name coverage.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEPT_HEADING_RE = re.compile(r"^##\s+(.+?)(?:\s+\(\d+명\))?\s*$")
EXCLUDED_NAME_PARTS = ("테스트", "관리자", "운영자", "test", "admin")
WDC_COMPANY_EMAIL_DOMAINS = frozenset({"jobkorea.co.kr", "worxphere.ai", "worxphere.ai.old"})
WDC_NAME_TEAM_RE = re.compile(r"^(?P<name>.+?)[_(](?P<team>[^_()]+)\)?$")
KOREAN_CHAR_RE = re.compile(r"[가-힣]")
HISTORY_VERSION = 1
STATUS_ORDER = {"active": 0, "unverified": 1, "former": 2}


@dataclass(frozen=True)
class Employee:
    identity_key: str
    name: str
    department: str
    position: str
    source: str


def _identity_key(*, email: str = "", employee_id: str = "", fallback: str = "") -> str:
    email = email.strip().lower()
    employee_id = employee_id.strip()
    if email:
        local_part, separator, domain = email.rpartition("@")
        # The same employee can have jobkorea.co.kr and worxphere.ai aliases.
        # Their normalized company-email local part is the stable join key; only
        # its one-way hash is persisted, never the address itself.
        material = (
            f"company-email:{local_part}"
            if separator and domain in WDC_COMPANY_EMAIL_DOMAINS
            else f"email:{email}"
        )
    elif employee_id:
        material = f"employee:{employee_id}"
    else:
        material = f"fallback:{fallback.strip()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def parse_familybab_index(path: Path) -> list[Employee]:
    employees: list[Employee] = []
    current_department = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        heading = DEPT_HEADING_RE.match(line)
        if heading:
            current_department = heading.group(1).strip()
            continue
        if not line.startswith("|") or "---" in line or "이름" in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        name = _clean_cell(cells[0])
        position = _clean_cell(cells[1])
        email = _clean_cell(cells[4]) if len(cells) > 4 else ""
        if not name or not current_department or _excluded_name(name):
            continue
        employees.append(
            Employee(
                identity_key=_identity_key(email=email, fallback=f"{name}|{current_department}"),
                name=name,
                department=current_department,
                position=position,
                source="familybab",
            )
        )
    return _dedupe(employees)


def parse_authoritative_roster(path: Path, familybab: list[Employee]) -> list[Employee]:
    """Read the private AI-subscription roster without exporting IDs or emails."""
    by_key = {employee.identity_key: employee for employee in familybab}
    employees: list[Employee] = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            name = str(row.get("employee_name") or "").strip()
            department = str(row.get("department_name") or "").strip()
            email = str(row.get("company_email") or "").strip().lower()
            employee_id = str(row.get("employee_id") or "").strip()
            if not name or not department or _excluded_name(name):
                continue
            key = _identity_key(email=email, employee_id=employee_id, fallback=f"{name}|{department}")
            familybab_match = by_key.get(key)
            employees.append(
                Employee(
                    identity_key=key,
                    name=name,
                    department=department,
                    position=familybab_match.position if familybab_match else "",
                    source="familybab",
                )
            )
    return _dedupe(employees)


def parse_wdc_notion_users(path: Path) -> list[Employee]:
    """Parse WDC users as coverage hints, never as current-employment proof."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    users = data.get("users") if isinstance(data, dict) else None
    if not isinstance(users, dict):
        return []

    employees: list[Employee] = []
    for entry in users.values():
        if not isinstance(entry, dict):
            continue
        email = str(entry.get("email") or "").strip().lower()
        domain = email.rsplit("@", 1)[-1] if "@" in email else ""
        if domain not in WDC_COMPANY_EMAIL_DOMAINS:
            continue
        raw_name = str(entry.get("name") or "").strip()
        name, department = _split_wdc_name_team(raw_name)
        if not name or not KOREAN_CHAR_RE.search(name) or _excluded_name(name):
            continue
        employees.append(
            Employee(
                identity_key=_identity_key(email=email, fallback=f"{name}|{department}"),
                name=name,
                department=department,
                position="",
                source="wdc",
            )
        )
    return _dedupe(employees)


def load_history(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": HISTORY_VERSION, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": HISTORY_VERSION, "entries": {}}
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        return {"version": HISTORY_VERSION, "entries": {}}
    return {"version": HISTORY_VERSION, "entries": entries}


def update_history(
    history: dict[str, Any],
    authoritative: list[Employee],
    supplemental: list[Employee],
    *,
    snapshot_date: str,
    source_is_fresh: bool,
) -> tuple[dict[str, Any], dict[str, int]]:
    entries = {key: dict(value) for key, value in history.get("entries", {}).items() if isinstance(value, dict)}
    current_keys = {employee.identity_key for employee in authoritative}
    current_name_counts: dict[str, int] = {}
    for employee in authoritative:
        current_name_counts[employee.name] = current_name_counts.get(employee.name, 0) + 1
    historical_name_keys: dict[str, list[str]] = {}
    for key, entry in entries.items():
        historical_name_keys.setdefault(str(entry.get("name") or ""), []).append(key)
    counts = {"activated": 0, "former": 0, "added_unverified": 0}

    for employee in authoritative:
        previous = entries.get(employee.identity_key, {})
        if (
            not previous
            and current_name_counts.get(employee.name) == 1
            and len(historical_name_keys.get(employee.name, [])) == 1
        ):
            previous_key = historical_name_keys[employee.name][0]
            previous = entries.pop(previous_key)
        status = "active" if source_is_fresh else str(previous.get("employment_status") or "unverified")
        if source_is_fresh and status != previous.get("employment_status"):
            counts["activated"] += 1
        previous_confirmed = str(previous.get("last_confirmed") or "")
        use_snapshot_fields = source_is_fresh or not previous or snapshot_date >= previous_confirmed
        entries[employee.identity_key] = {
            "name": employee.name if use_snapshot_fields else str(previous.get("name") or employee.name),
            "department": (
                employee.department
                if use_snapshot_fields
                else str(previous.get("department") or employee.department)
            ),
            "position": (
                employee.position or str(previous.get("position") or "")
                if use_snapshot_fields
                else str(previous.get("position") or employee.position)
            ),
            "employment_status": status,
            "first_seen": str(previous.get("first_seen") or snapshot_date),
            "last_confirmed": max(previous_confirmed, snapshot_date),
            "former_since": "" if source_is_fresh else str(previous.get("former_since") or ""),
            "ever_authoritative": True,
            "sources": sorted(set(previous.get("sources") or []) | {employee.source}),
        }

    if source_is_fresh:
        for key, entry in entries.items():
            if key in current_keys or not entry.get("ever_authoritative"):
                continue
            if entry.get("employment_status") != "former":
                entry["employment_status"] = "former"
                entry["former_since"] = snapshot_date
                counts["former"] += 1

    name_keys: dict[str, list[str]] = {}
    for key, entry in entries.items():
        name_keys.setdefault(str(entry.get("name") or ""), []).append(key)

    for employee in supplemental:
        resolved_key = employee.identity_key
        previous = entries.get(resolved_key)
        # FamilyBab and Notion can expose different company emails for the same
        # person. Merge only when the human-readable name has one unique match;
        # never collapse same-name people across departments.
        if previous is None and len(name_keys.get(employee.name, [])) == 1:
            resolved_key = name_keys[employee.name][0]
            previous = entries[resolved_key]
        if previous is None:
            entries[resolved_key] = {
                "name": employee.name,
                "department": employee.department,
                "position": employee.position,
                "employment_status": "unverified",
                "first_seen": snapshot_date,
                "last_confirmed": "",
                "former_since": "",
                "ever_authoritative": False,
                "sources": [employee.source],
            }
            name_keys.setdefault(employee.name, []).append(resolved_key)
            counts["added_unverified"] += 1
            continue
        previous["sources"] = sorted(set(previous.get("sources") or []) | {employee.source})
        if not previous.get("department") or previous.get("department") == "확인 필요":
            previous["department"] = employee.department

    return {"version": HISTORY_VERSION, "entries": entries}, counts


def render_roster(history: dict[str, Any]) -> str:
    rows = [value for value in history.get("entries", {}).values() if isinstance(value, dict)]
    rows.sort(
        key=lambda row: (
            STATUS_ORDER.get(str(row.get("employment_status")), 9),
            str(row.get("name") or ""),
            str(row.get("department") or ""),
        )
    )
    output = [["name", "department", "position", "employment_status", "last_confirmed", "source"]]
    for row in rows:
        output.append(
            [
                str(row.get("name") or ""),
                str(row.get("department") or ""),
                str(row.get("position") or ""),
                str(row.get("employment_status") or "unverified"),
                str(row.get("last_confirmed") or ""),
                ",".join(str(value) for value in row.get("sources") or []),
            ]
        )
    return "".join("\t".join(row) + "\n" for row in output)


def write_if_changed(path: Path, content: str) -> bool:
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
        Path(temporary_name).replace(path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return True


def _split_wdc_name_team(raw_name: str) -> tuple[str, str]:
    stripped = _strip_control_chars(raw_name).strip()
    stripped = re.sub(r"(?:님|씨)$", "", stripped).strip()
    match = WDC_NAME_TEAM_RE.match(stripped)
    if match:
        return match.group("name").strip(), match.group("team").strip()
    return stripped, "확인 필요"


def _clean_cell(value: str) -> str:
    return " ".join(_strip_control_chars(value).replace("\\|", "|").split())


def _strip_control_chars(value: str) -> str:
    return "".join(character for character in value if not unicodedata.category(character).startswith("C"))


def _excluded_name(value: str) -> bool:
    lowered = value.casefold()
    return any(part in lowered for part in EXCLUDED_NAME_PARTS)


def _dedupe(employees: list[Employee]) -> list[Employee]:
    by_key: dict[str, Employee] = {}
    for employee in employees:
        by_key.setdefault(employee.identity_key, employee)
    return sorted(by_key.values(), key=lambda employee: (employee.name, employee.department, employee.identity_key))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path, help="FamilyBab markdown snapshot")
    parser.add_argument("--historical-source", type=Path, help="older FamilyBab snapshot to seed former employees")
    parser.add_argument("--historical-source-mtime-epoch", type=float)
    parser.add_argument("--authoritative-roster", type=Path, help="AI subscription member-roster.csv")
    parser.add_argument("--wdc-users", type=Path, help="optional WDC identity export")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--history", required=True, type=Path)
    parser.add_argument("--status-out", required=True, type=Path)
    parser.add_argument("--source-max-age-hours", type=float, default=36.0)
    parser.add_argument("--source-mtime-epoch", type=float)
    parser.add_argument("--source-origin", default="unknown")
    parser.add_argument("--remote-refresh-result", default="not_needed")
    parser.add_argument("--local-fallback-result", default="not_needed")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    familybab = parse_familybab_index(args.source)
    if not familybab:
        raise SystemExit(f"no employees parsed from {args.source}")
    authoritative = (
        parse_authoritative_roster(args.authoritative_roster, familybab)
        if args.authoritative_roster and args.authoritative_roster.is_file()
        else familybab
    )
    supplemental = parse_wdc_notion_users(args.wdc_users) if args.wdc_users and args.wdc_users.is_file() else []

    source_epoch = args.source_mtime_epoch if args.source_mtime_epoch is not None else args.source.stat().st_mtime
    now = datetime.now(timezone.utc)
    age_hours = max(0.0, (now.timestamp() - source_epoch) / 3600)
    source_is_fresh = age_hours <= args.source_max_age_hours
    snapshot_date = datetime.fromtimestamp(source_epoch, timezone.utc).date().isoformat()
    old_history = load_history(args.history)
    historical_source_used = ""
    if args.historical_source and args.historical_source.is_file() and args.historical_source != args.source:
        historical_epoch = (
            args.historical_source_mtime_epoch
            if args.historical_source_mtime_epoch is not None
            else args.historical_source.stat().st_mtime
        )
        if historical_epoch < source_epoch:
            historical = parse_familybab_index(args.historical_source)
            historical_date = datetime.fromtimestamp(historical_epoch, timezone.utc).date().isoformat()
            old_history, _ = update_history(
                old_history,
                historical,
                [],
                snapshot_date=historical_date,
                source_is_fresh=False,
            )
            historical_source_used = str(args.historical_source)
    new_history, transitions = update_history(
        old_history,
        authoritative,
        supplemental,
        snapshot_date=snapshot_date,
        source_is_fresh=source_is_fresh,
    )
    roster_text = render_roster(new_history)
    status_counts: dict[str, int] = {"active": 0, "former": 0, "unverified": 0}
    for entry in new_history["entries"].values():
        status = str(entry.get("employment_status") or "unverified")
        status_counts[status] = status_counts.get(status, 0) + 1
    status = {
        "checked_at": now.replace(microsecond=0).isoformat(),
        "source": str(args.source),
        "source_origin": args.source_origin,
        "historical_source": historical_source_used,
        "source_snapshot_at": datetime.fromtimestamp(source_epoch, timezone.utc).replace(microsecond=0).isoformat(),
        "source_age_hours": round(age_hours, 2),
        "source_is_fresh": source_is_fresh,
        "source_max_age_hours": args.source_max_age_hours,
        "remote_refresh_result": args.remote_refresh_result,
        "local_fallback_result": args.local_fallback_result,
        "authoritative_count": len(authoritative),
        "supplemental_count": len(supplemental),
        "roster_counts": status_counts,
        "transitions": transitions,
    }

    if args.dry_run:
        action = "would update" if new_history != old_history or not args.out.is_file() else "unchanged"
        print(
            f"{action} roster: active={status_counts['active']} former={status_counts['former']} "
            f"unverified={status_counts['unverified']} source_fresh={str(source_is_fresh).lower()} "
            f"age_hours={age_hours:.1f}"
        )
        return 0

    history_changed = new_history != old_history
    if history_changed:
        history_payload = {**new_history, "updated_at": now.replace(microsecond=0).isoformat()}
        write_if_changed(args.history, json.dumps(history_payload, ensure_ascii=False, indent=2) + "\n")
    roster_changed = write_if_changed(args.out, roster_text)
    write_if_changed(args.status_out, json.dumps(status, ensure_ascii=False, indent=2) + "\n")
    action = "updated" if roster_changed else "unchanged"
    print(
        f"roster {action}: active={status_counts['active']} former={status_counts['former']} "
        f"unverified={status_counts['unverified']} source_fresh={str(source_is_fresh).lower()} "
        f"age_hours={age_hours:.1f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
