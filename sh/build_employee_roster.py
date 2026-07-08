#!/usr/bin/env python3
"""Build a compact meeting-safe employee roster from FamilyBab markdown.

Optionally widens name coverage with a WDC (worxphere-data-collectors) notion_users.json
identity export. WDC entries have no position and never override a FamilyBab row for
the same name (FamilyBab is the source of truth for department/position).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path


DEPT_HEADING_RE = re.compile(r"^##\s+(.+?)(?:\s+\(\d+명\))?\s*$")
EXCLUDED_NAME_PARTS = ("테스트", "관리자", "운영자", "test", "admin")

# Matches wdc's own COMPANY_EMAIL_DOMAINS (packages/indexer/src/wdc_indexer/identity.py) —
# only trust names tied to an internal-employee email, never external guests/vendors.
WDC_COMPANY_EMAIL_DOMAINS = frozenset({"jobkorea.co.kr", "worxphere.ai", "worxphere.ai.old"})
WDC_NAME_TEAM_RE = re.compile(r"^(?P<name>.+?)[_(](?P<team>[^_()]+)\)?$")
KOREAN_CHAR_RE = re.compile(r"[가-힣]")


@dataclass(frozen=True)
class Employee:
    name: str
    department: str
    position: str


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
        if not name or not current_department or _excluded_name(name):
            continue
        employees.append(Employee(name=name, department=current_department, position=position))
    return _dedupe(employees)


def parse_wdc_notion_users(path: Path) -> list[Employee]:
    """Parse wdc's notion_users.json export into name-only Employee rows (no position)."""
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
        employees.append(Employee(name=name, department=department, position=""))
    return employees


def _split_wdc_name_team(raw_name: str) -> tuple[str, str]:
    stripped = raw_name.replace("​", "").strip()
    stripped = re.sub(r"(?:님|씨)$", "", stripped).strip()
    match = WDC_NAME_TEAM_RE.match(stripped)
    if match:
        return match.group("name").strip(), match.group("team").strip()
    return stripped, "확인 필요"


def merge_rosters(primary: list[Employee], supplemental: list[Employee]) -> list[Employee]:
    """Merge two rosters; `primary` (FamilyBab) always wins for a name already present."""
    known_names = {employee.name for employee in primary}
    merged = list(primary)
    for employee in supplemental:
        if employee.name in known_names:
            continue
        known_names.add(employee.name)
        merged.append(employee)
    return merged


def write_roster(path: Path, employees: list[Employee]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t", lineterminator="\n")
        writer.writerow(["name", "department", "position"])
        for employee in employees:
            writer.writerow([employee.name, employee.department, employee.position])


def _clean_cell(value: str) -> str:
    return " ".join(value.replace("\\|", "|").split())


def _excluded_name(value: str) -> bool:
    lowered = value.casefold()
    return any(part in lowered for part in EXCLUDED_NAME_PARTS)


def _dedupe(employees: list[Employee]) -> list[Employee]:
    seen: set[tuple[str, str, str]] = set()
    result: list[Employee] = []
    for employee in sorted(employees, key=lambda e: (e.name, e.department, e.position)):
        key = (employee.name, employee.department, employee.position)
        if key in seen:
            continue
        seen.add(key)
        result.append(employee)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--wdc-users", type=Path, help="optional wdc notion_users.json to widen name coverage")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    employees = parse_familybab_index(args.source)
    if not employees:
        raise SystemExit(f"no employees parsed from {args.source}")

    wdc_added = 0
    if args.wdc_users and args.wdc_users.is_file():
        wdc_employees = parse_wdc_notion_users(args.wdc_users)
        merged = merge_rosters(employees, wdc_employees)
        wdc_added = len(merged) - len(employees)
        employees = merged

    if not args.dry_run:
        write_roster(args.out, employees)
    action = "would write" if args.dry_run else "wrote"
    print(f"{action} {len(employees)} employees to {args.out} (+{wdc_added} from wdc)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
