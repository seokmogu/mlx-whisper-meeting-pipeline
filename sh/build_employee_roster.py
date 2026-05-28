#!/usr/bin/env python3
"""Build a compact meeting-safe employee roster from FamilyBab markdown."""
from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path


DEPT_HEADING_RE = re.compile(r"^##\s+(.+?)(?:\s+\(\d+명\))?\s*$")
EXCLUDED_NAME_PARTS = ("테스트", "관리자", "운영자", "test", "admin")


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
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    employees = parse_familybab_index(args.source)
    if not employees:
        raise SystemExit(f"no employees parsed from {args.source}")
    if not args.dry_run:
        write_roster(args.out, employees)
    action = "would write" if args.dry_run else "wrote"
    print(f"{action} {len(employees)} employees to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
