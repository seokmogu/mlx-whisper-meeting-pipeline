#!/usr/bin/env python3
"""Render user-confirmed meeting attendees with optional identity disambiguation."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


def load_attendees(path: Path) -> list[str]:
    if not path.is_file():
        return []
    values: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").replace(
        "\n", ","
    ).split(","):
        name = raw.strip()
        if name and name not in values:
            values.append(name)
    return values


def load_identities(path: Path) -> dict[str, tuple[str, str]]:
    if not path.is_file():
        return {}
    result: dict[str, tuple[str, str]] = {}
    with path.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            name = (row.get("name") or "").strip()
            department = (row.get("department") or "").strip()
            position = (row.get("position") or "").strip()
            if name and department:
                result[name] = (department, position)
    return result


def identity_labels(
    attendees: list[str], identities: dict[str, tuple[str, str]]
) -> list[str]:
    values: list[str] = []
    for name in attendees:
        identity = identities.get(name)
        if identity is None:
            continue
        department, position = identity
        detail = ", ".join(value for value in (department, position) if value)
        values.append(f"{name}({detail})")
    return values


def render(attendees: list[str], identities: list[str], *, markdown: bool) -> str:
    if not attendees:
        return ""
    attendee_text = ", ".join(attendees)
    identity_text = "; ".join(identities)
    if markdown:
        lines = [f"- 사용자 확정 참석자: {attendee_text}"]
        if identity_text:
            lines.append(
                "- 사용자 확정 참석자 소속(동명이인 해소 근거): "
                f"{identity_text}"
            )
        return "\n".join(lines) + "\n"
    lines = ["사용자 확정 참석자:", attendee_text]
    if identity_text:
        lines.extend(
            [
                "",
                "사용자 확정 참석자 소속(동명이인 해소 근거):",
                identity_text,
            ]
        )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attendees", type=Path, required=True)
    parser.add_argument("--identities", type=Path, required=True)
    parser.add_argument("--markdown", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    attendees = load_attendees(args.attendees)
    identities = identity_labels(attendees, load_identities(args.identities))
    print(render(attendees, identities, markdown=args.markdown), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
