#!/usr/bin/env python3
"""Validate the reader-first Worxphere meeting-note contract."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


REQUIRED_HEADINGS = (
    "## 1. 핵심 결론 및 결정사항",
    "## 2. Action Items",
    "## 3. 미결 쟁점 및 다음 결정",
    "## 4. 상세 논의와 근거",
    "## 5. 참석자·용어 검증 부록",
)
FACT_CHECK_HEADING = "## 6. 객관 명제 팩트체크"
ALLOWED_HEADINGS = (*REQUIRED_HEADINGS, FACT_CHECK_HEADING)
REQUIRED_APPENDIX_HEADINGS = (
    "### 5.1 참석자/언급 인물",
    "### 5.2 검증 완료",
    "### 5.3 검증 필요",
)
SUMMARY_MAX_CHARS = 1400
SUMMARY_MAX_ITEMS = 7
HEADING_RE = re.compile(r"^##\s+", re.MULTILINE)
SUBHEADING_RE = re.compile(r"^#{2,3}\s+", re.MULTILINE)
SUMMARY_ITEM_RE = re.compile(r"^(?:[-*+]\s+|\d+[.)]\s+)")
ACTION_ID_RE = re.compile(r"^A[1-9]\d*$")
ACTION_ITEM_RE = re.compile(
    r"^- \[(?: |x|X)\] \*\*(A[1-9]\d*)\s+·\s+(.+?)\*\*\s*$"
)
ACTION_META_RE = re.compile(
    r"^(?:\t| {2,})(.+?)\s+·\s+(.+?)\s+·\s+(§4\.\d+(?:\s*,\s*§4\.\d+)*)\s*$"
)
OPEN_ID_RE = re.compile(r"^M[1-9]\d*$")
OPEN_ITEM_RE = re.compile(r"^- \*\*(M[1-9]\d*)\s+·\s+(.+?)\*\*\s*$")
OPEN_META_RE = ACTION_META_RE
ACTION_FIELD_LABEL_RE = re.compile(
    r"(?:담당|기한|완료 기준|검증 상태|근거(?:/의존성)?):"
)
ANONYMIZED_OWNER_RE = re.compile(r"(?:^|[\s(/])화자\s+[A-Z?](?:$|[\s)/])")
UNCERTAIN_VERIFICATION_RE = re.compile(r"추정|후보|가능성|불명확|확인 필요")
COMPLETED_SOURCE_RE = re.compile(r"`([^`]+)`\s*(?:→|->)")
BACKTICK_RE = re.compile(r"`([^`]+)`")
NUMERIC_SOURCE_RE = re.compile(r"\d")
SOURCE_SUFFIX_RE = re.compile(r"(?:까지|부터|에서|으로|로|에게|한테|에|은|는|이|가|을|를)$")
REPORTING_ENDINGS = (
    "설명했다",
    "언급됐다",
    "제기됐다",
    "논의했다",
    "공유됐다",
    "확인됐다",
    "의견이 나왔다",
)
BILINGUAL_PRODUCT_PATTERNS = {
    "니카(Nika)": re.compile(r"니카\(Nika\)"),
    "잡코리아(JK)": re.compile(r"잡코리아\(JK\)"),
    "나인하이어(Ninehire)": re.compile(r"나인하이어\(Ninehire\)"),
}
ROSTER_BOILERPLATE_RE = re.compile(
    r"직원 디렉토리에서\s*active로 확인|직원 디렉토리.*확인됨"
)


def _section_body(text: str, heading: str) -> str:
    start = text.find(heading)
    if start < 0:
        return ""
    body_start = text.find("\n", start)
    if body_start < 0:
        return ""
    next_heading = HEADING_RE.search(text, body_start + 1)
    end = next_heading.start() if next_heading else len(text)
    return text[body_start + 1 : end].strip()


def _subsection_body(text: str, heading: str) -> str:
    start = text.find(heading)
    if start < 0:
        return ""
    body_start = text.find("\n", start)
    if body_start < 0:
        return ""
    next_heading = SUBHEADING_RE.search(text, body_start + 1)
    end = next_heading.start() if next_heading else len(text)
    return text[body_start + 1 : end].strip()


def _normalize_verification_source(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip())
    return SOURCE_SUFFIX_RE.sub("", normalized)


def _reporting_ending(line: str) -> str | None:
    stripped = line.rstrip().rstrip(".")
    return next((ending for ending in REPORTING_ENDINGS if stripped.endswith(ending)), None)


def _naturalness_errors(text: str) -> list[str]:
    errors: list[str] = []
    primary_text = "\n".join(
        _section_body(text, heading) for heading in REQUIRED_HEADINGS[:4]
    )
    for label, pattern in BILINGUAL_PRODUCT_PATTERNS.items():
        count = len(pattern.findall(primary_text))
        if count > 2:
            errors.append(
                f"Korean naturalness B-1 repeats bilingual product name {label}: {count} times"
            )

    detail = _section_body(text, "## 4. 상세 논의와 근거")
    bullets = [
        line.strip()
        for line in detail.splitlines()
        if line.strip().startswith(("- ", "* "))
    ]
    endings = [_reporting_ending(line) for line in bullets]
    run_ending: str | None = None
    run_length = 0
    for ending in endings:
        if ending is not None and ending == run_ending:
            run_length += 1
        elif ending is not None:
            run_ending = ending
            run_length = 1
        else:
            run_ending = None
            run_length = 0
        if run_length >= 4:
            errors.append(
                f"Korean naturalness E-2 repeats reporting ending '{ending}' in {run_length} consecutive detail bullets"
            )
            break

    reporting = [ending for ending in endings if ending is not None]
    if len(bullets) >= 12 and len(reporting) >= 8:
        ratio = len(reporting) / len(bullets)
        if ratio >= 0.45:
            errors.append(
                "Korean naturalness E-2 formulaic reporting endings dominate detail bullets: "
                f"{len(reporting)}/{len(bullets)} ({ratio:.0%})"
            )
    if reporting:
        most_common = max(set(reporting), key=reporting.count)
        count = reporting.count(most_common)
        if count >= 10 and count / max(len(bullets), 1) >= 0.25:
            errors.append(
                f"Korean naturalness E-2 overuses reporting ending '{most_common}': {count}/{len(bullets)} detail bullets"
            )
    attendee_body = _subsection_body(text, "### 5.1 참석자/언급 인물")
    roster_boilerplate = len(ROSTER_BOILERPLATE_RE.findall(attendee_body))
    if roster_boilerplate >= 2:
        errors.append(
            "Korean naturalness repeats employee-roster boilerplate in attendee list: "
            f"{roster_boilerplate} occurrences"
        )
    return errors


def validate_note(text: str) -> list[str]:
    errors: list[str] = []
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not first_line.startswith("# ") or first_line.startswith("## "):
        errors.append("first non-empty line must be a specific H1 Markdown title")

    actual_headings = tuple(
        match.group(0).strip()
        for match in re.finditer(r"^##\s+.+$", text, re.MULTILINE)
    )
    for heading in REQUIRED_HEADINGS:
        if heading not in actual_headings:
            errors.append(f"missing required section: {heading}")
    for heading in actual_headings:
        if heading not in ALLOWED_HEADINGS:
            errors.append(f"unexpected H2 section: {heading}")
    expected_headings = REQUIRED_HEADINGS + (
        (FACT_CHECK_HEADING,) if FACT_CHECK_HEADING in actual_headings else ()
    )
    if all(heading in actual_headings for heading in REQUIRED_HEADINGS) and actual_headings != expected_headings:
        errors.append("required sections are not in contract order")

    appendix_positions: list[int] = []
    for heading in REQUIRED_APPENDIX_HEADINGS:
        position = text.find(heading)
        if position < 0:
            errors.append(f"missing required appendix subsection: {heading}")
        else:
            appendix_positions.append(position)
    if (
        len(appendix_positions) == len(REQUIRED_APPENDIX_HEADINGS)
        and appendix_positions != sorted(appendix_positions)
    ):
        errors.append("appendix subsections are not in contract order")

    if re.search(r"^##\s+.*Task Handoff", text, re.MULTILINE):
        errors.append("Task Handoff is forbidden; keep one canonical Action Items checklist")

    summary = _section_body(text, "## 1. 핵심 결론 및 결정사항")
    if summary:
        summary_chars = len(summary)
        if summary_chars > SUMMARY_MAX_CHARS:
            errors.append(
                f"핵심 결론 및 결정사항 exceeds {SUMMARY_MAX_CHARS} characters: {summary_chars}"
            )
        summary_items = sum(
            1 for line in summary.splitlines() if SUMMARY_ITEM_RE.match(line.strip())
        )
        if summary_items == 0:
            errors.append("핵심 결론 및 결정사항 must contain at least one bullet or numbered item")
        elif summary_items > SUMMARY_MAX_ITEMS:
            errors.append(
                f"핵심 결론 및 결정사항 exceeds {SUMMARY_MAX_ITEMS} items: {summary_items}"
            )

    action_body = _section_body(text, "## 2. Action Items")
    if action_body and action_body.strip() != "- 해당 없음":
        lines = action_body.splitlines()
        action_ids: set[str] = set()
        action_texts: set[str] = set()
        action_count = 0
        for index, line in enumerate(lines):
            match = ACTION_ITEM_RE.fullmatch(line.strip())
            if not match:
                continue
            action_count += 1
            action_id, action_text = match.groups()
            if not ACTION_ID_RE.fullmatch(action_id):
                errors.append(f"invalid action ID: {action_id}")
            elif action_id in action_ids:
                errors.append(f"duplicate action ID: {action_id}")
            action_ids.add(action_id)
            if action_text in action_texts:
                errors.append(f"duplicate action text: {action_text}")
            action_texts.add(action_text)
            if text.count(action_text) > 1:
                errors.append(f"action text appears outside its canonical home: {action_id}")

            meta_match = (
                ACTION_META_RE.fullmatch(lines[index + 1])
                if index + 1 < len(lines)
                else None
            )
            if not meta_match:
                errors.append(
                    f"action {action_id} must be followed by one owner · due · §4.x metadata line"
                )
            elif ANONYMIZED_OWNER_RE.search(meta_match.group(1)):
                errors.append(f"action {action_id} uses an anonymized speaker as owner")

        if action_count == 0:
            errors.append("Action Items must use the canonical compact checklist")
        expected_action_ids = {f"A{index}" for index in range(1, action_count + 1)}
        if action_ids != expected_action_ids:
            errors.append("Action Items IDs must be contiguous after final classification")
        if "|" in action_body:
            errors.append("Action Items must not use a Markdown table")
        if ACTION_FIELD_LABEL_RE.search(action_body):
            errors.append("Action Items must not repeat per-action field labels")

    open_body = _section_body(text, "## 3. 미결 쟁점 및 다음 결정")
    if open_body and open_body.strip() != "- 해당 없음":
        lines = open_body.splitlines()
        open_ids: set[str] = set()
        open_count = 0
        for index, line in enumerate(lines):
            match = OPEN_ITEM_RE.fullmatch(line.strip())
            if not match:
                continue
            open_count += 1
            open_id, open_text = match.groups()
            if open_id in open_ids:
                errors.append(f"duplicate open-item ID: {open_id}")
            open_ids.add(open_id)
            if text.count(open_text) > 1:
                errors.append(f"open-item text appears outside its canonical home: {open_id}")
            meta_match = (
                OPEN_META_RE.fullmatch(lines[index + 1])
                if index + 1 < len(lines)
                else None
            )
            if not meta_match:
                errors.append(
                    f"open item {open_id} must be followed by one owner · due · §4.x metadata line"
                )
            elif ANONYMIZED_OWNER_RE.search(meta_match.group(1)):
                errors.append(f"open item {open_id} uses an anonymized speaker as owner")
        if open_count == 0:
            errors.append("미결 쟁점 및 다음 결정 must use the canonical compact list")
        expected_open_ids = {f"M{index}" for index in range(1, open_count + 1)}
        if open_ids != expected_open_ids:
            errors.append("open-item IDs must be contiguous after final classification")

    for heading in REQUIRED_HEADINGS[:4]:
        body = _section_body(text, heading)
        if "<table" in body or any(
            line.lstrip().startswith("|") for line in body.splitlines()
        ):
            errors.append(f"{heading} must not use a table")
        if BACKTICK_RE.search(body):
            errors.append(f"{heading} must not use inline code")

    completed_verification = _subsection_body(text, "### 5.2 검증 완료")
    pending_verification = _subsection_body(text, "### 5.3 검증 필요")
    if UNCERTAIN_VERIFICATION_RE.search(completed_verification):
        errors.append("검증 완료 contains uncertainty language")
    numeric_sources = [
        source
        for source in COMPLETED_SOURCE_RE.findall(completed_verification)
        if NUMERIC_SOURCE_RE.search(source)
    ]
    if numeric_sources:
        errors.append(
            "검증 완료 contains protected numeric/date source: "
            + ", ".join(numeric_sources)
        )
    completed_sources = {
        _normalize_verification_source(source)
        for source in COMPLETED_SOURCE_RE.findall(completed_verification)
    }
    pending_sources = {
        _normalize_verification_source(source)
        for source in BACKTICK_RE.findall(pending_verification)
    }
    overlapping_sources = completed_sources & pending_sources
    if overlapping_sources:
        errors.append(
            "verification source appears in both completed and pending sections: "
            + ", ".join(sorted(overlapping_sources))
        )

    fact_check = _section_body(text, FACT_CHECK_HEADING)
    if FACT_CHECK_HEADING in actual_headings:
        if "- 웹검색:" not in fact_check:
            errors.append("objective fact-check section must report web-search execution state")
        fact_ids = [
            int(value)
            for value in re.findall(r"^### F([1-9]\d*)[.]\s+", fact_check, re.MULTILINE)
        ]
        if fact_ids and fact_ids != list(range(1, len(fact_ids) + 1)):
            errors.append("objective fact-check IDs must be contiguous F1..Fn")
        if "객관 사실과 충돌" in fact_check:
            if "- 바로잡기:" not in fact_check or "- 틀린 이유:" not in fact_check:
                errors.append("contradicted fact-check item needs correction and explanation")
            if not re.search(r"^- 근거: .+", fact_check, re.MULTILINE):
                errors.append("contradicted fact-check item needs evidence")
            urls = re.findall(r"\]\(([^)]+)\)", fact_check)
            if urls and any(not url.startswith("https://") for url in urls):
                errors.append("objective fact-check links must use direct HTTPS URLs")

    errors.extend(_naturalness_errors(text))

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("note", type=Path)
    args = parser.parse_args()
    text = args.note.read_text(encoding="utf-8")
    errors = validate_note(text)
    if errors:
        for error in errors:
            print(f"invalid meeting note output: {error}")
        return 1
    print("meeting note contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
