#!/usr/bin/env python3
"""Render locally reviewable meeting notes and optionally publish them to Notion.

The local meeting pipeline always creates the canonical Markdown note and then
this module creates a deterministic human-review projection. Publication stays
separate and optional. Actual Notion writes are delegated to
``run-notion-publish-agent.sh`` so Notion failures cannot block transcription.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import html
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterable


PRIVATE_DATA_SOURCE_DEFAULT = "7483a1ab-d3cb-4b4d-b626-309ec554d7d1"
PUBLIC_DATA_SOURCE_DEFAULT = "1f62af1e-16e1-4679-9485-d7c349d28559"
AUTHOR_NAME_DEFAULT = "구석모_AIProduct팀"
AUTHOR_USER_ID_DEFAULT = "2a8d872b-594c-812a-a61b-0002a4cd405c"
PRIVATE_MARKER_RE = re.compile(
    r"(?<![0-9A-Za-z가-힣])비공개(?![0-9A-Za-z가-힣])"
)
PUBLIC_MARKER_RE = re.compile(
    r"(?<![0-9A-Za-z가-힣])공개(?![0-9A-Za-z가-힣])"
)
H1_RE = re.compile(r"^#\s+(.+?)\s*$")
H2_RE = re.compile(r"^##\s+(.+?)\s*$")
DATE_STEM_RE = re.compile(r"(?:^|[_\s-])20\d{6}(?:[_\s-]?\d{6})?(?:$|[_\s-])")
NAME_RE = re.compile(r"^[가-힣]{2,4}$")
GENERIC_LABELS = {
    "미팅",
    "회의",
    "녹음",
    "음성",
    "새로운",
    "참석자",
    "비공개",
    "meeting",
}


@dataclass(frozen=True)
class PublicationMetadata:
    source_path: str
    readable_path: str
    source_sha256: str
    readable_sha256: str
    title: str
    meeting_datetime: str
    summary: str
    attendees: list[str]
    author_name: str
    author_user_id: str
    source_label: str
    visibility: str
    target_data_source_id: str
    target_name: str
    visual_assets: list[dict[str, Any]]


@dataclass(frozen=True)
class ActionItem:
    action_id: str
    checked: bool
    title: str
    owner: str
    timing: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class OpenItem:
    item_id: str
    title: str
    owner: str
    timing: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class VisualNode:
    node_id: str
    label: str
    detail: str = ""
    source_ref: str = ""


@dataclass(frozen=True)
class VisualizationSpec:
    kind: str
    title: str
    reason: str
    nodes: tuple[VisualNode, ...]
    groups: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class ReadableRender:
    text: str
    actions: tuple[ActionItem, ...]
    open_items: tuple[OpenItem, ...]
    evidence_titles: dict[str, str]
    visual_assets: tuple[dict[str, str], ...]


def repo_base() -> Path:
    return Path(__file__).resolve().parents[1]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default.copy()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default.copy()
    return value if isinstance(value, dict) else default.copy()


def save_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _load_note_validator() -> Any:
    path = repo_base() / "sh" / "validate_meeting_note.py"
    spec = importlib.util.spec_from_file_location("notion_publication_note_validator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load canonical meeting-note validator: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical_note_coordinates(source: Path, base: Path) -> tuple[str, str]:
    try:
        relative = source.resolve().relative_to((base / "notes").resolve())
    except ValueError as exc:
        raise ValueError(f"meeting note must be under {base / 'notes'}: {source}") from exc
    if len(relative.parts) != 2 or relative.suffix.lower() != ".md":
        raise ValueError("meeting note must use notes/<project>/<meeting>.md")
    return relative.parts[0], source.stem


def review_stem(stem: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"\s+", "_", stem)).strip("_")


def review_dir_for(source: Path, base: Path) -> Path:
    _project, stem = canonical_note_coordinates(source, base)
    reviewer_root = Path(
        os.getenv(
            "MEETING_CONTEXT_REVIEWER_DIR",
            str(base.parent / "meeting-context-reviewer"),
        )
    ).expanduser()
    return reviewer_root / "reviews" / review_stem(stem)


def readiness_path_for(source: Path, base: Path) -> Path:
    key = relative_note_key(source, base)
    safe = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", key).strip("_")
    return base / "state" / "notion-publication" / "readiness" / f"{safe}.json"


def validate_readiness_receipt(
    *,
    source: Path,
    transcript: Path,
    candidate: Path,
    review_dir: Path,
    base: Path,
) -> None:
    path = readiness_path_for(source, base)
    receipt = load_json(path, {})
    if not receipt:
        raise RuntimeError(f"publication readiness receipt is missing or invalid: {path}")
    if receipt.get("schema_version") != 1:
        raise RuntimeError("publication readiness receipt must use schema_version 1")
    expected = {
        "source_path": str(source.resolve()),
        "source_sha256": sha256_file(source),
        "transcript_path": str(transcript.resolve()),
        "transcript_sha256": sha256_file(transcript),
        "readable_path": str(candidate.resolve()),
        "readable_sha256": sha256_file(candidate),
        "review_dir": str(review_dir.resolve()),
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise RuntimeError(f"publication readiness receipt {key} does not match current artifacts")
    artifacts = receipt.get("review_artifacts")
    required_artifacts = {
        "review.md",
        "review.json",
        "wiki-update-candidates.md",
    }
    if not isinstance(artifacts, dict) or set(artifacts) != required_artifacts:
        raise RuntimeError("publication readiness receipt must hash every required review artifact")
    for name in required_artifacts:
        if artifacts.get(name) != sha256_file(review_dir / name):
            raise RuntimeError(f"publication readiness receipt review artifact is stale: {name}")


def validate_queue_admission(source: Path, base: Path) -> tuple[Path, dict[str, Any]]:
    project, stem = canonical_note_coordinates(source, base)
    if not source.is_file():
        raise FileNotFoundError(f"meeting note does not exist: {source}")
    transcript = base / "transcripts" / project / f"{stem}.txt"
    if not transcript.is_file() or not transcript.read_text(encoding="utf-8").strip():
        raise RuntimeError(f"completed transcript is missing or empty: {transcript}")

    note_text = source.read_text(encoding="utf-8")
    errors = _load_note_validator().validate_note(note_text)
    if errors:
        raise RuntimeError("canonical meeting note validation failed: " + "; ".join(errors))

    candidate = readable_path_for(source, base)
    if not candidate.is_file() or not candidate.read_text(encoding="utf-8").strip():
        raise RuntimeError(f"readable projection is missing or empty: {candidate}")
    assets = visual_assets_for(candidate)
    marker = assets[0].get("local_markdown") if assets else None
    projection = validate_projection(
        note_text,
        candidate.read_text(encoding="utf-8"),
        visual_marker=marker,
    )
    if projection.get("faithful") is not True:
        raise RuntimeError("readable projection is not fresh for the canonical meeting note")
    for asset in assets:
        for path_key, hash_key in (
            ("svg_path", "svg_sha256"),
            ("embed_html_path", "embed_html_sha256"),
        ):
            asset_path = Path(str(asset.get(path_key, "")))
            if not asset_path.is_file() or asset.get(hash_key) != sha256_file(asset_path):
                raise RuntimeError(f"readable visual asset is missing or stale: {asset_path}")

    review_dir = review_dir_for(source, base)
    missing = [
        name
        for name in ("review.md", "review.json", "wiki-update-candidates.md")
        if not (review_dir / name).is_file()
        or not (review_dir / name).stat().st_size
    ]
    if missing:
        raise RuntimeError(
            "completed context review artifacts are missing: "
            + ", ".join(str(review_dir / name) for name in missing)
        )
    try:
        review_payload = json.loads((review_dir / "review.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("completed context review JSON is invalid") from exc
    if not isinstance(review_payload, dict):
        raise RuntimeError("completed context review JSON must be an object")
    validate_readiness_receipt(
        source=source,
        transcript=transcript,
        candidate=candidate,
        review_dir=review_dir,
        base=base,
    )
    return candidate, projection


def relative_note_key(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path.resolve())


def resolve_note_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    candidate = base / path
    return candidate if candidate.exists() else path.resolve()


def read_pending(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def write_pending(path: Path, values: Iterable[str]) -> None:
    unique = list(dict.fromkeys(values))
    atomic_write_text(path, "".join(f"{value}\n" for value in unique))


def enqueue(
    *,
    base: Path,
    pending_file: Path,
    files: Iterable[str],
) -> list[str]:
    queued = read_pending(pending_file)
    added: list[str] = []
    for raw in files:
        value = raw.strip()
        if not value:
            continue
        path = resolve_note_path(value, base)
        if path.name.endswith("_notion-readable.md") or path.suffix.lower() != ".md":
            continue
        validate_queue_admission(path, base)
        key = relative_note_key(path, base)
        if key not in queued:
            queued.append(key)
            added.append(key)
    write_pending(pending_file, queued)
    return added


def reconcile_changed_labels(
    *,
    base: Path,
    pending_file: Path,
    state_path: Path,
) -> list[str]:
    """Requeue published notes whose mutable Voice Memo title changed."""

    state = load_json(state_path, {"files": {}})
    files = state.get("files")
    if not isinstance(files, dict):
        return []
    changed: list[str] = []
    for key, publication in files.items():
        if not isinstance(key, str) or not isinstance(publication, dict):
            continue
        source = resolve_note_path(key, base)
        if not source.is_file():
            continue
        _project, _stem, current_label = recording_context(source, base)
        current_visibility = resolve_visibility(source, base, current_label)
        current_author = publication_author_name()
        current_author_user_id = publication_author_user_id()
        current_source_sha256 = sha256_file(source)
        if (
            publication.get("source_label") != current_label
            or publication.get("visibility") != current_visibility
            or publication.get("author_name") != current_author
            or publication.get("author_user_id") != current_author_user_id
            or publication.get("source_sha256") != current_source_sha256
        ):
            changed.append(key)
    if not changed:
        return []
    return enqueue(base=base, pending_file=pending_file, files=changed)


def file_list(path: Path | None) -> list[str]:
    if path is None:
        return []
    if not path.exists():
        raise FileNotFoundError(f"file list does not exist: {path}")
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def first_h1(content: str) -> str:
    for line in content.splitlines():
        match = H1_RE.match(line.strip())
        if match:
            title = match.group(1).strip()
            if title and title != "미팅노트":
                return title
            break
    raise ValueError("meeting note must have a specific H1 title")


def _outside_fence_heading_indices(lines: list[str]) -> list[int]:
    indices: list[int] = []
    fence: str | None = None
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        fence_match = re.match(r"(`{3,}|~{3,})", stripped)
        if fence_match:
            marker = fence_match.group(1)
            if fence is None:
                fence = marker[0]
            elif marker[0] == fence:
                fence = None
            continue
        if fence is None and H2_RE.match(line.strip()):
            indices.append(index)
    return indices


def _indent_block(lines: list[str]) -> list[str]:
    return [("\t" + line) if line else "" for line in lines]


def _split_h2_sections(text: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    indices = _outside_fence_heading_indices(lines)
    if not indices:
        return lines, []
    prefix = lines[: indices[0]]
    sections: list[tuple[str, list[str]]] = []
    for position, start in enumerate(indices):
        end = indices[position + 1] if position + 1 < len(indices) else len(lines)
        heading = H2_RE.match(lines[start].strip())
        assert heading is not None
        sections.append((heading.group(1).strip(), lines[start + 1 : end]))
    return prefix, sections


def _evidence_titles(sections: list[tuple[str, list[str]]]) -> dict[str, str]:
    titles: dict[str, str] = {}
    for heading, body in sections:
        if not heading.startswith("4."):
            continue
        for line in body:
            match = re.match(r"^###\s+(4\.\d+)\s+(.+?)\s*$", line.strip())
            if match:
                titles[match.group(1)] = match.group(2).strip()
    return titles


def _parse_actions(sections: list[tuple[str, list[str]]]) -> tuple[ActionItem, ...]:
    body = next((lines for heading, lines in sections if "Action Items" in heading), [])
    actions: list[ActionItem] = []
    index = 0
    while index < len(body):
        match = re.match(
            r"^\s*-\s*\[([ xX])\]\s*\*\*(A\d+)\s*[·.]\s*(.+?)\*\*\s*$",
            body[index],
        )
        if not match:
            index += 1
            continue
        metadata = ""
        cursor = index + 1
        while cursor < len(body) and not body[cursor].strip():
            cursor += 1
        if cursor < len(body) and not re.match(r"^\s*-\s*\[", body[cursor]):
            metadata = body[cursor].strip()
        parts = [part.strip() for part in metadata.split(" · ")]
        if len(parts) < 3:
            raise ValueError(f"action {match.group(2)} has invalid metadata: {metadata!r}")
        refs = tuple(dict.fromkeys(re.findall(r"§(4\.\d+)", parts[-1])))
        actions.append(
            ActionItem(
                action_id=match.group(2),
                checked=match.group(1).lower() == "x",
                title=match.group(3).strip(),
                owner=parts[0],
                timing=" · ".join(parts[1:-1]),
                evidence_refs=refs,
            )
        )
        index = max(cursor + 1, index + 1)
    return tuple(actions)


def _parse_open_items(sections: list[tuple[str, list[str]]]) -> tuple[OpenItem, ...]:
    body = next(
        (lines for heading, lines in sections if "미결 쟁점" in heading),
        [],
    )
    items: list[OpenItem] = []
    index = 0
    while index < len(body):
        match = re.match(r"^\s*-\s*\*\*(M\d+)\s*[·.]\s*(.+?)\*\*\s*$", body[index])
        if not match:
            index += 1
            continue
        cursor = index + 1
        while cursor < len(body) and not body[cursor].strip():
            cursor += 1
        metadata = body[cursor].strip() if cursor < len(body) else ""
        parts = [part.strip() for part in metadata.split(" · ")]
        if len(parts) < 3:
            raise ValueError(f"open item {match.group(1)} has invalid metadata: {metadata!r}")
        refs = tuple(dict.fromkeys(re.findall(r"§(4\.\d+)", parts[-1])))
        items.append(
            OpenItem(
                item_id=match.group(1),
                title=match.group(2).strip(),
                owner=parts[0],
                timing=" · ".join(parts[1:-1]),
                evidence_refs=refs,
            )
        )
        index = max(cursor + 1, index + 1)
    return tuple(items)


def _heading_anchor(ref: str, title: str) -> str:
    value = re.sub(r"[^0-9A-Za-z가-힣 -]+", "", f"{ref} {title}").strip().lower()
    return "#" + re.sub(r"[ -]+", "-", value)


def _evidence_links(action: ActionItem, titles: dict[str, str]) -> str:
    values: list[str] = []
    for ref in action.evidence_refs:
        title = titles.get(ref, "근거")
        label = f"{ref} {title}"
        values.append(f"[{label}]({_heading_anchor(ref, title)})")
    return ", ".join(values) if values else "확인 필요"


def _refs_as_links(refs: tuple[str, ...], titles: dict[str, str]) -> str:
    values: list[str] = []
    for ref in refs:
        title = titles.get(ref, "근거")
        values.append(f"[{ref} {title}]({_heading_anchor(ref, title)})")
    return ", ".join(values) if values else "확인 필요"


def _expand_inline_evidence(line: str, titles: dict[str, str]) -> str:
    return re.sub(
        r"§(4\.\d+)",
        lambda match: (
            f"[{match.group(1)} {titles.get(match.group(1), '근거')}]"
            f"({_heading_anchor(match.group(1), titles.get(match.group(1), '근거'))})"
        ),
        line,
    )


def _trim_blank_lines(lines: list[str]) -> list[str]:
    start = 0
    end = len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _primary_visual_text(
    source_text: str,
    sections: list[tuple[str, list[str]]],
) -> str:
    values = [first_h1(source_text)]
    for heading, body in sections:
        if heading.startswith("1."):
            values.extend(_trim_blank_lines(body))
        if heading.startswith("4."):
            values.extend(line for line in body if line.strip().startswith("### "))
    return "\n".join(values)


def _first_evidence_ref_for_term(
    term_pattern: str,
    sections: list[tuple[str, list[str]]],
) -> str:
    current_ref = ""
    current_title = ""
    for heading, body in sections:
        if not heading.startswith("4."):
            continue
        for line in body:
            subheading = re.match(r"^###\s+(4\.\d+)\s+(.+?)\s*$", line.strip())
            if subheading:
                current_ref = subheading.group(1)
                current_title = subheading.group(2).strip()
            if re.search(term_pattern, line, flags=re.IGNORECASE):
                return f"{current_ref} {current_title}".strip()
    return ""


def _rollout_visual_spec(
    source_text: str,
    sections: list[tuple[str, list[str]]],
) -> tuple[int, VisualizationSpec] | None:
    primary = _primary_visual_text(source_text, sections)
    stages = [
        ("공개", r"공개|릴리즈|release"),
        ("온보딩", r"온보딩|교육"),
        ("파일럿", r"파일럿|pilot"),
        ("품질 검증", r"\bQC\b|\bBVT\b|검증|테스트"),
        ("피드백 반영", r"피드백|보완|수정"),
        ("확산", r"확산|전면 적용"),
        ("운영 이관", r"운영 이관|챔피언 이관|현업.*이관"),
    ]
    matches: list[tuple[int, VisualNode]] = []
    for index, (label, pattern) in enumerate(stages, 1):
        match = re.search(pattern, primary, flags=re.IGNORECASE)
        if not match:
            continue
        matches.append(
            (
                match.start(),
                VisualNode(
                    node_id=f"S{index}",
                    label=label,
                    detail=match.group(0),
                    source_ref=_first_evidence_ref_for_term(pattern, sections),
                ),
            )
        )
    strong = sum(node.label in {"공개", "온보딩", "파일럿", "확산"} for _, node in matches)
    if len(matches) < 4 or strong < 2:
        return None
    ordered = [node for _, node in sorted(matches, key=lambda item: item[0])]
    nodes = tuple(
        VisualNode(
            node_id=f"S{index}",
            label=node.label,
            detail=node.detail,
            source_ref=node.source_ref,
        )
        for index, node in enumerate(ordered, 1)
    )
    title_hits = len(re.findall(r"공개|온보딩|파일럿|확산", first_h1(source_text), re.IGNORECASE))
    score = len(nodes) * 3 + strong * 2 + title_hits * 4
    return score, VisualizationSpec(
        kind="rollout-map",
        title="도입·확산 요소 지도",
        reason="공개, 온보딩, 검증, 확산 요소가 회의의 주된 관계로 관측됨",
        nodes=nodes,
    )


def _system_visual_spec(
    source_text: str,
    sections: list[tuple[str, list[str]]],
) -> tuple[int, VisualizationSpec] | None:
    primary = _primary_visual_text(source_text, sections)
    entities = [
        ("layer1", "Layer 1", r"Layer\s*1|레이어\s*1"),
        ("layer2", "Layer 2", r"Layer\s*2|레이어\s*2"),
        ("layer3", "Layer 3", r"Layer\s*3|레이어\s*3"),
        ("ready-data", "AI Ready Data", r"AI[- ]?ready Data|AI Ready Data"),
        ("nika", "니카(Nika)", r"니카\(Nika\)|니카"),
        ("mcp", "MCP", r"\bMCP\b"),
        ("skill", "Skill", r"\bSkill\b"),
        ("marketplace", "Marketplace", r"marketplace|마켓플레이스"),
        ("control-plane", "Control Plane", r"Control Plane|컨트롤 플레인"),
        ("vanguard", "AX Vanguard", r"AX Vanguard"),
    ]
    nodes: list[VisualNode] = []
    for node_id, label, pattern in entities:
        if re.search(pattern, primary, flags=re.IGNORECASE):
            nodes.append(
                VisualNode(
                    node_id=node_id,
                    label=label,
                    source_ref=_first_evidence_ref_for_term(pattern, sections),
                )
            )
    layer_ids = {node.node_id for node in nodes if node.node_id.startswith("layer")}
    connection_hits = len(re.findall(r"연결|구조|아키텍처|레이어|Layer", primary, re.IGNORECASE))
    if len(nodes) < 6 or len(layer_ids) < 2 or connection_hits < 2:
        return None
    node_ids = {node.node_id for node in nodes}
    group_defs = [
        ("공통 기반", ("layer1",)),
        ("데이터·컨텍스트", ("layer2", "ready-data")),
        ("개발·에이전트", ("layer3", "nika")),
        ("운영 연결", ("mcp", "skill", "marketplace", "control-plane", "vanguard")),
    ]
    groups = tuple(
        (label, tuple(node_id for node_id in ids if node_id in node_ids))
        for label, ids in group_defs
        if any(node_id in node_ids for node_id in ids)
    )
    score = len(nodes) * 2 + len(layer_ids) * 4 + min(connection_hits, 6)
    return score, VisualizationSpec(
        kind="system-map",
        title="시스템·레이어 연결 구조",
        reason="여러 레이어와 운영 구성요소의 연결이 회의의 주된 관계로 관측됨",
        nodes=tuple(nodes),
        groups=groups,
    )


def _milestone_visual_spec(actions: tuple[ActionItem, ...]) -> tuple[int, VisualizationSpec] | None:
    nodes: list[VisualNode] = []
    distinct_dates: set[str] = set()
    date_pattern = re.compile(
        r"(?:20\d{2}[-./]\d{1,2}[-./]\d{1,2}|\d{1,2}월\s*\d{1,2}일|\d{1,2}/\d{1,2})"
    )
    for action in actions:
        matches = date_pattern.findall(action.timing)
        if not matches:
            continue
        distinct_dates.update(matches)
        nodes.append(
            VisualNode(
                node_id=action.action_id,
                label=action.title,
                detail=action.timing,
                source_ref=action.evidence_refs[0] if action.evidence_refs else "",
            )
        )
    if len(nodes) < 3 or len(distinct_dates) < 2:
        return None
    return len(nodes) * 3 + len(distinct_dates) * 2, VisualizationSpec(
        kind="milestone-timeline",
        title="명시 일정·마일스톤",
        reason="서로 다른 명시 날짜에 연결된 액션이 세 건 이상 관측됨",
        nodes=tuple(nodes),
    )


def _decision_visual_spec(items: tuple[OpenItem, ...]) -> tuple[int, VisualizationSpec] | None:
    decision_pattern = re.compile(
        r"할지|인지|여부|둘지|나눌지|어느|어디|누가|어떻게|몇\s*점|먼저"
    )
    matched = [item for item in items if decision_pattern.search(item.title)]
    if len(items) < 5 or len(matched) < 5:
        return None
    nodes = tuple(
        VisualNode(
            node_id=item.item_id,
            label=item.title,
            detail=item.timing,
            source_ref=item.evidence_refs[0] if item.evidence_refs else "",
        )
        for item in items
    )
    return len(matched) * 3, VisualizationSpec(
        kind="decision-map",
        title="다음 결정 지도",
        reason="여러 선택·범위·확정 항목이 회의의 다음 판단으로 관측됨",
        nodes=nodes,
    )


def select_visualization_spec(
    source_text: str,
    actions: tuple[ActionItem, ...],
    open_items: tuple[OpenItem, ...],
) -> VisualizationSpec | None:
    _prefix, sections = _split_h2_sections(source_text)
    candidates = [
        candidate
        for candidate in (
            _rollout_visual_spec(source_text, sections),
            _system_visual_spec(source_text, sections),
            _milestone_visual_spec(actions),
            _decision_visual_spec(open_items),
        )
        if candidate is not None
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _svg_start(title: str, desc: str, height: int) -> list[str]:
    return [
        '<svg xmlns="http://www.w3.org/2000/svg" width="960" '
        f'height="{height}" viewBox="0 0 960 {height}" role="img" '
        'aria-labelledby="title desc">',
        "<style>",
        "text{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;fill:#242424}",
        ".heading{font-size:24px;font-weight:600}.label{font-size:15px;font-weight:600}",
        ".detail{font-size:12px;fill:#6f7378}.node{fill:#f7f8fa;stroke:#dfe3e8}",
        ".accent{fill:#e8f2ff;stroke:#8bb7e8}.line{stroke:#93a4b7;stroke-width:2;fill:none}",
        ".dot{fill:#2468b4}.id{font-size:12px;font-weight:700;fill:#2468b4}",
        "</style>",
        f'<title id="title">{html.escape(title)}</title>',
        f'<desc id="desc">{html.escape(desc)}</desc>',
        f'<rect width="960" height="{height}" fill="#ffffff"/>',
        f'<text class="heading" x="32" y="42">{html.escape(title)}</text>',
    ]


def _short(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _wrap_text(value: str, width: int, max_lines: int = 2) -> list[str]:
    words = value.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) == max_lines - 1:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    consumed = " ".join(lines)
    if len(consumed) < len(value) and lines:
        lines[-1] = _short(lines[-1], max(width - 1, 2)) + "…"
    return lines[:max_lines]


def _svg_text_lines(
    lines: list[str],
    *,
    x: float,
    y: float,
    class_name: str,
    line_gap: int = 20,
) -> list[str]:
    rendered = [f'<text class="{class_name}" x="{x}" y="{y}">']
    for index, line in enumerate(lines):
        dy = 0 if index == 0 else line_gap
        rendered.append(
            f'<tspan x="{x}" dy="{dy}">{html.escape(line)}</tspan>'
        )
    rendered.append("</text>")
    return rendered


def _render_rollout_svg(spec: VisualizationSpec, meeting_title: str) -> str:
    columns = 3
    rows = (len(spec.nodes) + columns - 1) // columns
    height = 110 + rows * 112
    lines = _svg_start(
        f"{meeting_title} · {spec.title}",
        spec.reason,
        height,
    )
    lines.append('<text class="detail" x="32" y="68">회의에 명시된 요소이며 선후 순서를 의미하지 않음</text>')
    gap = 16
    width = (896 - gap * (columns - 1)) / columns
    for index, node in enumerate(spec.nodes):
        column = index % columns
        row = index // columns
        x = 32 + column * (width + gap)
        y = 92 + row * 112
        lines.extend(
            [
                f'<rect class="accent" x="{x}" y="{y}" width="{width}" height="88" rx="8"/>',
                f'<text class="id" x="{x + 14}" y="{y + 23}">{html.escape(node.node_id)}</text>',
                f'<text class="label" x="{x + 14}" y="{y + 49}">{html.escape(_short(node.label, 12))}</text>',
                f'<text class="detail" x="{x + 14}" y="{y + 71}">{html.escape(_short(node.source_ref or node.detail, 18))}</text>',
            ]
        )
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def _render_system_svg(spec: VisualizationSpec, meeting_title: str) -> str:
    node_by_id = {node.node_id: node for node in spec.nodes}
    max_nodes = max((len(ids) for _label, ids in spec.groups), default=1)
    height = 170 + max_nodes * 48
    lines = _svg_start(f"{meeting_title} · {spec.title}", spec.reason, height)
    group_count = len(spec.groups)
    gap = 18
    width = (896 - gap * (group_count - 1)) / max(group_count, 1)
    y = 92
    for index, (group_label, node_ids) in enumerate(spec.groups):
        x = 32 + index * (width + gap)
        group_height = 54 + len(node_ids) * 42
        if index < group_count - 1:
            lines.append(
                f'<line class="line" x1="{x + width}" y1="{y + 28}" x2="{x + width + gap}" y2="{y + 28}"/>'
            )
        lines.append(f'<rect class="accent" x="{x}" y="{y}" width="{width}" height="{group_height}" rx="8"/>')
        lines.append(f'<text class="label" x="{x + 14}" y="{y + 28}">{html.escape(group_label)}</text>')
        node_y = y + 45
        for node_id in node_ids:
            node = node_by_id[node_id]
            lines.extend(
                [
                    f'<rect class="node" x="{x + 12}" y="{node_y}" width="{width - 24}" height="32" rx="5"/>',
                    f'<text class="id" x="{x + 22}" y="{node_y + 21}">{html.escape(node.label)}</text>',
                ]
            )
            node_y += 42
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def _render_milestone_svg(spec: VisualizationSpec, meeting_title: str) -> str:
    height = 390
    lines = _svg_start(f"{meeting_title} · {spec.title}", spec.reason, height)
    y = 185
    lines.append(f'<line class="line" x1="70" y1="{y}" x2="890" y2="{y}"/>')
    count = len(spec.nodes)
    for index, node in enumerate(spec.nodes):
        x = 90 + index * (780 / max(count - 1, 1))
        upper = index % 2 == 0
        card_y = 88 if upper else 220
        lines.extend(
            [
                f'<line class="line" x1="{x}" y1="{y}" x2="{x}" y2="{card_y + (72 if upper else 0)}"/>',
                f'<circle class="dot" cx="{x}" cy="{y}" r="6"/>',
                f'<rect class="node" x="{x - 95}" y="{card_y}" width="190" height="72" rx="7"/>',
                f'<text class="id" x="{x - 82}" y="{card_y + 19}">{html.escape(node.node_id)} · {html.escape(_short(node.detail, 18))}</text>',
                f'<text class="label" x="{x - 82}" y="{card_y + 43}">{html.escape(_short(node.label, 20))}</text>',
            ]
        )
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def _render_decision_svg(spec: VisualizationSpec, meeting_title: str) -> str:
    columns = 2
    rows = (len(spec.nodes) + columns - 1) // columns
    height = 96 + rows * 132
    lines = _svg_start(f"{meeting_title} · {spec.title}", spec.reason, height)
    lines.append('<text class="detail" x="32" y="68">각 카드는 독립된 미결 결정이며 선후관계를 의미하지 않음</text>')
    for index, node in enumerate(spec.nodes):
        column = index % columns
        row = index // columns
        x = 32 + column * 456
        y = 88 + row * 132
        wrapped = _wrap_text(node.label, 42, max_lines=2)
        lines.extend(
            [
                f'<rect class="node" x="{x}" y="{y}" width="424" height="108" rx="8"/>',
                f'<text class="id" x="{x + 16}" y="{y + 22}">{html.escape(node.node_id)}</text>',
            ]
        )
        lines.extend(_svg_text_lines(wrapped, x=x + 54, y=y + 22, class_name="label"))
        lines.extend(
            [
                f'<text class="detail" x="{x + 54}" y="{y + 70}">{html.escape(_short(node.detail, 35))}</text>',
                f'<text class="detail" x="{x + 54}" y="{y + 91}">{html.escape(node.source_ref)}</text>',
            ]
        )
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def render_visual_svg(spec: VisualizationSpec, meeting_title: str) -> str:
    renderers = {
        "rollout-map": _render_rollout_svg,
        "system-map": _render_system_svg,
        "milestone-timeline": _render_milestone_svg,
        "decision-map": _render_decision_svg,
    }
    try:
        renderer = renderers[spec.kind]
    except KeyError as exc:
        raise ValueError(f"unsupported visualization kind: {spec.kind}") from exc
    return renderer(spec, meeting_title)


def _render_action_section(
    heading: str,
    actions: tuple[ActionItem, ...],
    titles: dict[str, str],
    visual_marker: str | None,
) -> list[str]:
    rendered = [f"## {heading}", ""]
    if visual_marker:
        rendered.extend([visual_marker, ""])
    for action in actions:
        checked = "x" if action.checked else " "
        rendered.append(
            f"- [{checked}] **{action.action_id} · {action.title}** — "
            f"**담당** · {action.owner} · **일정** · {action.timing} · "
            f"**근거** · {_evidence_links(action, titles)}"
        )
    rendered.append("")
    return rendered


def _render_open_section(
    heading: str,
    items: tuple[OpenItem, ...],
    titles: dict[str, str],
) -> list[str]:
    rendered = [f"## {heading}", ""]
    for item in items:
        rendered.append(
            f"- **{item.item_id} · {item.title}** — "
            f"**담당** · {item.owner} · **결정 시점** · {item.timing} · "
            f"**근거** · {_refs_as_links(item.evidence_refs, titles)}"
        )
    rendered.append("")
    return rendered


def render_readable_text(
    source_text: str,
    *,
    visual_marker: str | None = None,
) -> ReadableRender:
    """Create a standard-Markdown human review projection from a canonical note."""

    normalized = source_text.replace("\r\n", "\n").replace("\r", "\n")
    prefix, sections = _split_h2_sections(normalized)
    if not sections:
        raise ValueError("meeting note must have H2 sections")
    h1_index = next(
        (index for index, line in enumerate(prefix) if H1_RE.match(line.strip())),
        None,
    )
    if h1_index is None:
        raise ValueError("meeting note must have an H1 title")
    titles = _evidence_titles(sections)
    actions = _parse_actions(sections)
    open_items = _parse_open_items(sections)

    first_h2 = len(prefix)
    metadata_start = h1_index + 1
    while metadata_start < first_h2 and not prefix[metadata_start].strip():
        metadata_start += 1
    metadata = [line.strip() for line in prefix[metadata_start:] if line.strip()]
    rendered: list[str] = [prefix[h1_index].strip(), ""]
    rendered.extend(f"> {line}" for line in metadata)
    rendered.append("")

    for position, (heading, body) in enumerate(sections):
        if position:
            while rendered and not rendered[-1].strip():
                rendered.pop()
            rendered.extend(["", "---", ""])
        if "Action Items" in heading and actions:
            rendered.extend(_render_action_section(heading, actions, titles, visual_marker))
            continue
        if "미결 쟁점" in heading and open_items:
            rendered.extend(_render_open_section(heading, open_items, titles))
            continue
        rendered.append(f"## {heading}")
        rendered.append("")
        visible_body = _trim_blank_lines(body)
        if heading.startswith("1."):
            visible_body = [_expand_inline_evidence(line, titles) for line in visible_body]
        rendered.extend(visible_body)

    return ReadableRender(
        text="\n".join(rendered).rstrip() + "\n",
        actions=actions,
        open_items=open_items,
        evidence_titles=titles,
        visual_assets=(),
    )


def default_validator() -> Path:
    configured = os.getenv("NOTION_READABLE_VALIDATOR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return (
        Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex")))
        / "skills"
        / "notion-readable-meeting-minutes"
        / "scripts"
        / "validate_lossless_minutes.py"
    )


def validate_lossless(
    source: Path,
    candidate: Path,
    validator: Path,
    *,
    candidate_title: str | None = None,
) -> dict[str, Any]:
    if not validator.is_file():
        raise FileNotFoundError(f"lossless validator not found: {validator}")
    command = [sys.executable, str(validator), str(source), str(candidate)]
    if candidate_title is not None:
        command.extend(["--candidate-title", candidate_title])
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"lossless validator returned invalid JSON: {result.stderr.strip()}"
        ) from exc
    if result.returncode != 0 or payload.get("lossless") is not True:
        raise RuntimeError(
            "Notion-readable candidate failed lossless validation: "
            + json.dumps(payload, ensure_ascii=False)
        )
    return payload


def validate_projection(
    source_text: str,
    candidate_text: str,
    *,
    visual_marker: str | None = None,
) -> dict[str, Any]:
    expected = render_readable_text(source_text, visual_marker=visual_marker)
    exact = expected.text == candidate_text
    return {
        "faithful": exact,
        "source_sha256": sha256_text(source_text),
        "readable_sha256": sha256_text(candidate_text),
        "deterministic_projection_exact": exact,
        "action_count": len(expected.actions),
        "open_item_count": len(expected.open_items),
        "evidence_heading_count": len(expected.evidence_titles),
        "visualization_included": visual_marker is not None,
        "source_title": first_h1(source_text),
        "readable_title": first_h1(candidate_text),
    }


def readable_path_for(source: Path, base: Path) -> Path:
    content = source.read_text(encoding="utf-8")
    meeting_datetime = parse_meeting_datetime(source, content)
    meeting_date = meeting_datetime[:10] if meeting_datetime else "undated"
    project, _stem, _label = recording_context(source, base)
    project = project or "unscoped"
    return base / "notion-readable" / project / meeting_date / f"{source.stem}.md"


def visual_manifest_path(candidate: Path) -> Path:
    return candidate.with_suffix(".assets.json")


def visual_assets_for(candidate: Path) -> list[dict[str, Any]]:
    payload = load_json(visual_manifest_path(candidate), {"assets": []})
    assets = payload.get("assets")
    if not isinstance(assets, list):
        return []
    return [item for item in assets if isinstance(item, dict)]


def archive_superseded_visual_assets(
    candidate: Path,
    previous_assets: list[dict[str, Any]],
    current_assets: list[dict[str, Any]],
) -> None:
    current_paths = {
        str(value)
        for asset in current_assets
        for key in ("svg_path", "embed_html_path")
        if (value := asset.get(key))
    }
    archive_dir = candidate.parent / "superseded-visuals"
    for asset in previous_assets:
        for key in ("svg_path", "embed_html_path"):
            raw_path = asset.get(key)
            if not isinstance(raw_path, str) or raw_path in current_paths:
                continue
            path = Path(raw_path)
            if not path.is_file():
                continue
            archive_dir.mkdir(parents=True, exist_ok=True)
            target = archive_dir / path.name
            if target.exists():
                target = archive_dir / f"{path.stem}-{sha256_file(path)[:8]}{path.suffix}"
            path.replace(target)


def create_readable_candidate(
    source: Path,
    *,
    base: Path,
    validator: Path,
) -> tuple[Path, dict[str, Any]]:
    del validator  # exact validation is used later between the approved preview and live export
    source_text = source.read_text(encoding="utf-8")
    candidate = readable_path_for(source, base)
    previous_assets = visual_assets_for(candidate)
    initial = render_readable_text(source_text)
    assets: list[dict[str, Any]] = []
    marker: str | None = None
    visual_spec = select_visualization_spec(
        source_text,
        initial.actions,
        initial.open_items,
    )
    if visual_spec is not None:
        svg_path = candidate.with_name(f"{source.stem}-{visual_spec.kind}.svg")
        html_path = candidate.with_name(f"{source.stem}-{visual_spec.kind}.html")
        svg = render_visual_svg(visual_spec, first_h1(source_text))
        embedded_html = (
            "<!doctype html><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<style>html,body{margin:0;background:#fff}svg{display:block;width:100%;height:auto}</style>"
            + svg
        )
        atomic_write_text(svg_path, svg)
        atomic_write_text(html_path, embedded_html)
        marker = f"![{visual_spec.title}]({svg_path.name})"
        assets.append(
            {
                "kind": visual_spec.kind,
                "alt": visual_spec.title,
                "reason": visual_spec.reason,
                "visualization_spec": asdict(visual_spec),
                "local_markdown": marker,
                "svg_path": str(svg_path.resolve()),
                "svg_sha256": sha256_file(svg_path),
                "embed_html_path": str(html_path.resolve()),
                "embed_html_sha256": sha256_file(html_path),
            }
        )
    rendered = render_readable_text(source_text, visual_marker=marker)
    if not candidate.exists() or candidate.read_text(encoding="utf-8") != rendered.text:
        atomic_write_text(candidate, rendered.text)
    archive_superseded_visual_assets(candidate, previous_assets, assets)
    save_json(visual_manifest_path(candidate), {"assets": assets})
    validation = validate_projection(source_text, rendered.text, visual_marker=marker)
    validation["visualization_kind"] = visual_spec.kind if visual_spec else "none"
    if validation.get("faithful") is not True:
        raise RuntimeError(
            "Notion-readable projection validation failed: "
            + json.dumps(validation, ensure_ascii=False)
        )
    return candidate, validation


def recording_context(source: Path, base: Path) -> tuple[str, str, str]:
    """Return project, stable stem, and latest mutable recording label."""

    try:
        relative = source.resolve().relative_to((base / "notes").resolve())
    except ValueError:
        return "", source.stem, source.stem
    project = relative.parts[0] if len(relative.parts) > 1 else ""
    stem = source.stem
    title_path = base / "state" / "voice-memo-titles" / project / f"{stem}.txt"
    if title_path.is_file():
        label = " ".join(title_path.read_text(encoding="utf-8").split()).strip()
        if label:
            return project, stem, label
    return project, stem, stem


def is_private_label(label: str) -> bool:
    return PRIVATE_MARKER_RE.search(label) is not None


def explicit_visibility(source: Path, base: Path, label: str) -> str | None:
    project, stem, _label = recording_context(source, base)
    visibility_path = (
        base / "state" / "meeting-visibility" / project / f"{stem}.txt"
    )
    if visibility_path.is_file():
        value = visibility_path.read_text(encoding="utf-8").strip().lower()
        if value not in {"public", "private"}:
            raise ValueError(
                f"invalid meeting visibility metadata: {visibility_path} -> {value!r}"
            )
        return value
    if is_private_label(label):
        return "private"
    if PUBLIC_MARKER_RE.search(label):
        return "public"
    return None


def resolve_visibility(source: Path, base: Path, label: str) -> str:
    return explicit_visibility(source, base, label) or (
        "private" if is_private_label(label) else "public"
    )


def publication_author_name() -> str:
    value = os.getenv("NOTION_MEETING_AUTHOR_NAME", AUTHOR_NAME_DEFAULT).strip()
    if not value:
        raise ValueError("NOTION_MEETING_AUTHOR_NAME must not be empty")
    return value


def publication_author_user_id() -> str:
    value = os.getenv(
        "NOTION_MEETING_AUTHOR_USER_ID", AUTHOR_USER_ID_DEFAULT
    ).strip()
    if not value:
        raise ValueError("NOTION_MEETING_AUTHOR_USER_ID must not be empty")
    return value


def load_roster_names(base: Path) -> set[str]:
    roster = base / "glossary" / "employee_roster.tsv"
    if not roster.is_file():
        return set()
    names: set[str] = set()
    for line in roster.read_text(encoding="utf-8").splitlines():
        name = line.split("\t", 1)[0].strip()
        if name:
            names.add(name)
    return names


def parse_attendee_names(value: str, roster_names: set[str]) -> list[str]:
    cleaned = PRIVATE_MARKER_RE.sub(" ", value)
    cleaned = re.sub(r"(?i)^\s*참석자\s*:\s*", "", cleaned)
    cleaned = DATE_STEM_RE.sub(" ", cleaned)
    cleaned = re.sub(r"(?i)\bmeeting\b", " ", cleaned)
    cleaned = re.sub(r"(?:^|[\s_-])미팅(?:$|[\s_-])", " ", cleaned)
    raw_tokens = re.split(r"[,，+&/\s_-]+", cleaned)
    names: list[str] = []
    for raw in raw_tokens:
        token = raw.strip()
        if not token or token.lower() in GENERIC_LABELS:
            continue
        if roster_names:
            if token not in roster_names:
                continue
        elif not NAME_RE.fullmatch(token):
            continue
        if token not in names:
            names.append(token)
    return names


def resolve_attendees(source: Path, base: Path, source_label: str) -> list[str]:
    project, stem, _label = recording_context(source, base)
    roster_names = load_roster_names(base)
    confirmed = base / "state" / "meeting-attendees" / project / f"{stem}.txt"
    if confirmed.is_file():
        names = parse_attendee_names(
            confirmed.read_text(encoding="utf-8"),
            roster_names,
        )
        if names:
            return names
    names = parse_attendee_names(source_label, roster_names)
    if names:
        return names

    content = source.read_text(encoding="utf-8")
    for line in content.splitlines():
        match = re.match(
            r"^\s*-\s*(?:참석자|참여자|확정 참석자(?:\([^)]*\))?)\s*:\s*(.+)$",
            line,
            flags=re.IGNORECASE,
        )
        if match:
            names.extend(parse_attendee_names(match.group(1), roster_names))
    return list(dict.fromkeys(names))


def parse_meeting_datetime(source: Path, content: str) -> str:
    metadata = re.search(
        r"(?m)^-\s*일시:\s*(\d{4}-\d{2}-\d{2})"
        r"(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?",
        content,
    )
    if metadata:
        date_part = metadata.group(1)
        hour = metadata.group(2)
        if hour is None:
            return date_part
        minute = metadata.group(3) or "00"
        second = metadata.group(4) or "00"
        return f"{date_part}T{hour}:{minute}:{second}+09:00"

    match = re.search(r"(20\d{6})[_\s-]?(\d{6})", source.stem)
    if match:
        parsed = datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
        return parsed.strftime("%Y-%m-%dT%H:%M:%S+09:00")
    match = re.search(r"(20\d{6})", source.stem)
    if match:
        parsed = datetime.strptime(match.group(1), "%Y%m%d")
        return parsed.strftime("%Y-%m-%d")
    return ""


def _plain_inline(value: str) -> str:
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"`([^`]*)`", r"\1", value)
    value = re.sub(r"(\*\*|__)(.+?)\1", r"\2", value)
    value = re.sub(r"~~(.+?)~~", r"\1", value)
    return value.strip()


def extract_summary(content: str) -> str:
    lines = content.splitlines()
    in_summary = False
    values: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped in {
            "## 1. 핵심 결론 및 결정사항",
            "## 1. 핵심 요약",
        }:
            in_summary = True
            continue
        if in_summary and H2_RE.match(stripped):
            break
        if not in_summary or not stripped:
            continue
        value = re.sub(r"^(?:[-+*]|\d+[.)])\s+", "", stripped)
        value = _plain_inline(value)
        if value:
            values.append(value)
    return "\n".join(values)[:2000]


def build_metadata(
    source: Path,
    candidate: Path,
    *,
    base: Path,
) -> PublicationMetadata:
    content = source.read_text(encoding="utf-8")
    _project, _stem, label = recording_context(source, base)
    visibility = resolve_visibility(source, base, label)
    if visibility == "private":
        target_data_source_id = os.getenv(
            "NOTION_PRIVATE_DATA_SOURCE_ID", PRIVATE_DATA_SOURCE_DEFAULT
        )
        target_name = "내부미팅 v2"
    else:
        target_data_source_id = os.getenv(
            "NOTION_PUBLIC_DATA_SOURCE_ID", PUBLIC_DATA_SOURCE_DEFAULT
        )
        target_name = "AI Product 팀 회의록"
    return PublicationMetadata(
        source_path=str(source.resolve()),
        readable_path=str(candidate.resolve()),
        source_sha256=sha256_file(source),
        readable_sha256=sha256_file(candidate),
        title=first_h1(content),
        meeting_datetime=parse_meeting_datetime(source, content),
        summary=extract_summary(content),
        attendees=resolve_attendees(source, base, label),
        author_name=publication_author_name(),
        author_user_id=publication_author_user_id(),
        source_label=label,
        visibility=visibility,
        target_data_source_id=target_data_source_id,
        target_name=target_name,
        visual_assets=visual_assets_for(candidate),
    )


def request_path_for(source: Path, base: Path) -> Path:
    key = relative_note_key(source, base)
    safe = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", key).strip("_")
    return base / "state" / "notion-publication" / "requests" / f"{safe}.json"


def receipt_path_for(source: Path, base: Path) -> Path:
    key = relative_note_key(source, base)
    safe = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", key).strip("_")
    return base / "state" / "notion-publication" / "receipts" / f"{safe}.json"


def prepare(
    source: Path,
    *,
    base: Path,
    validator: Path,
    state: dict[str, Any],
) -> tuple[PublicationMetadata, dict[str, Any], Path]:
    del validator
    candidate, validation = validate_queue_admission(source, base)
    metadata = build_metadata(source, candidate, base=base)
    key = relative_note_key(source, base)
    previous = state.get("files", {}).get(key)
    request: dict[str, Any] = {
        **asdict(metadata),
        "projection_validation": validation,
        "existing_publication": previous if isinstance(previous, dict) else None,
    }
    request_path = request_path_for(source, base)
    save_json(request_path, request)
    return metadata, validation, request_path


APPROVAL_FIELDS = (
    "approval_id",
    "request_path",
    "request_sha256",
    "source_path",
    "source_sha256",
    "readable_path",
    "readable_sha256",
    "target_data_source_id",
    "expires_at",
)


def _required_string(payload: dict[str, Any], key: str, *, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{label} must contain a non-empty {key}")
    return value


def load_approval(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"publication approval file not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"publication approval file is invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"publication approval file must be a JSON object: {path}")
    for key in APPROVAL_FIELDS:
        _required_string(value, key, label="publication approval file")
    return value


def request_snapshot(request_path: Path, base: Path) -> dict[str, str]:
    expected_dir = (base / "state" / "notion-publication" / "requests").resolve()
    try:
        request_path.resolve().relative_to(expected_dir)
    except ValueError as exc:
        raise RuntimeError(f"publication request must be under {expected_dir}: {request_path}") from exc
    request = load_json(request_path, {})
    if not request:
        raise RuntimeError(f"publication request is missing or invalid: {request_path}")
    fields = (
        "source_path",
        "source_sha256",
        "readable_path",
        "readable_sha256",
        "target_data_source_id",
    )
    return {
        key: _required_string(request, key, label="publication request") for key in fields
    }


def validate_approval_file(
    approval_file: Path,
    request_path: Path,
    *,
    base: Path,
) -> dict[str, Any]:
    approval = load_approval(approval_file)
    snapshot = request_snapshot(request_path, base)
    expected = {
        "request_path": str(request_path.resolve()),
        "request_sha256": sha256_file(request_path),
        **snapshot,
    }
    for key, value in expected.items():
        if approval.get(key) != value:
            raise RuntimeError(f"publication approval {key} does not match the prepared request")
    try:
        expires_at = datetime.fromisoformat(
            _required_string(approval, "expires_at", label="publication approval file").replace(
                "Z", "+00:00"
            )
        )
    except ValueError as exc:
        raise RuntimeError("publication approval expires_at must be ISO-8601 with a timezone") from exc
    if expires_at.tzinfo is None:
        raise RuntimeError("publication approval expires_at must include a timezone")
    if expires_at <= datetime.now(timezone.utc):
        raise RuntimeError("publication approval has expired")

    source = Path(snapshot["source_path"])
    candidate = Path(snapshot["readable_path"])
    if not source.is_file() or sha256_file(source) != snapshot["source_sha256"]:
        raise RuntimeError("publication source changed after the approval request was prepared")
    if not candidate.is_file() or sha256_file(candidate) != snapshot["readable_sha256"]:
        raise RuntimeError("publication readable projection changed after the approval request was prepared")
    if candidate.resolve() != readable_path_for(source, base).resolve():
        raise RuntimeError("publication request readable path is not the canonical projection path")
    validate_queue_admission(source, base)
    return approval


def assert_metadata_snapshot_fresh(metadata: PublicationMetadata) -> None:
    source = Path(metadata.source_path)
    candidate = Path(metadata.readable_path)
    if not source.is_file() or sha256_file(source) != metadata.source_sha256:
        raise RuntimeError("publication source changed after preparation")
    if not candidate.is_file() or sha256_file(candidate) != metadata.readable_sha256:
        raise RuntimeError("publication readable projection changed after preparation")


def _receipt_success(receipt: dict[str, Any]) -> bool:
    return (
        receipt.get("status") in {"created", "updated", "skipped"}
        and receipt.get("post_fetch_verified") is True
        and isinstance(receipt.get("page_id"), str)
        and bool(receipt.get("page_id"))
    )


def validate_receipt_identity(
    receipt: dict[str, Any], metadata: PublicationMetadata
) -> None:
    expected = {
        "visibility": metadata.visibility,
        "target_data_source_id": metadata.target_data_source_id,
        "title": metadata.title,
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise RuntimeError(f"publication receipt {key} does not match the prepared request")


def live_export_path_for(source: Path, base: Path) -> Path:
    key = relative_note_key(source, base)
    safe = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", key).strip("_")
    return base / "state" / "notion-publication" / "exports" / f"{safe}.md"


def normalize_notion_markdown_escapes(markdown: str) -> str:
    """Collapse Notion's doubled escapes before Markdown-significant punctuation."""
    return re.sub(r"\\\\(?=[\\*~`$\[\]<>{}|^_])", r"\\", markdown)


def verify_live_receipt(
    receipt: dict[str, Any],
    *,
    source: Path,
    candidate: Path,
    base: Path,
    validator: Path,
    expected_title: str,
    visual_assets: list[dict[str, Any]],
) -> dict[str, Any]:
    fetched_markdown = receipt.get("fetched_markdown")
    if not isinstance(fetched_markdown, str) or not fetched_markdown.strip():
        raise RuntimeError("publication receipt has no fresh fetched Markdown body")
    exported = live_export_path_for(source, base)
    exported_markdown = fetched_markdown.rstrip() + "\n"
    atomic_write_text(exported, exported_markdown)
    validation_target = exported
    normalized = normalize_notion_markdown_escapes(exported_markdown)
    if visual_assets:
        embed_pattern = re.compile(
            r"(?m)^<embed\b[^>]*(?:/>|>.*?</embed>)\s*$",
            re.DOTALL,
        )
        for asset in visual_assets:
            local_markdown = asset.get("local_markdown", "")
            if not local_markdown:
                continue
            normalized, count = embed_pattern.subn(local_markdown, normalized, count=1)
            if count != 1:
                raise RuntimeError("live Notion export is missing an expected visual embed")
    if normalized != exported_markdown:
        validation_target = exported.with_name(exported.stem + "-normalized.md")
        atomic_write_text(validation_target, normalized)
    return validate_lossless(
        candidate,
        validation_target,
        validator,
        candidate_title=expected_title,
    )


def invoke_agent(
    agent: Path,
    request_path: Path,
    receipt_path: Path,
    *,
    publish: bool,
    approval_file: Path | None = None,
) -> dict[str, Any]:
    command = [
        str(agent),
        "--request",
        str(request_path),
        "--receipt",
        str(receipt_path),
        "--publish" if publish else "--preflight",
    ]
    if publish:
        if approval_file is None:
            raise RuntimeError("live publication requires an explicit approval file")
        command.extend(["--approval-file", str(approval_file)])
    result = subprocess.run(command, check=False, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Notion publication agent failed with exit {result.returncode}")
    receipt = load_json(receipt_path, {})
    if not receipt:
        raise RuntimeError("Notion publication agent returned no receipt")
    return receipt


def pending_sort_key(value: str, base: Path) -> tuple[str, str]:
    path = resolve_note_path(value, base)
    content = path.read_text(encoding="utf-8") if path.is_file() else ""
    return (parse_meeting_datetime(path, content), value)


def run_queue(args: argparse.Namespace) -> int:
    base = Path(args.base).resolve()
    pending_file = Path(args.pending_file).expanduser()
    state_path = Path(args.state).expanduser()
    state = load_json(state_path, {"files": {}})
    if not isinstance(state.get("files"), dict):
        state["files"] = {}
    pending = read_pending(pending_file)
    if args.only:
        requested_only = relative_note_key(resolve_note_path(args.only, base), base)
        pending = [value for value in pending if value == requested_only]
    if args.visibility:
        pending = [
            value
            for value in pending
            if explicit_visibility(
                resolve_note_path(value, base),
                base,
                recording_context(resolve_note_path(value, base), base)[2],
            )
            == args.visibility
        ]
    pending.sort(key=lambda value: pending_sort_key(value, base), reverse=True)
    selected = pending[: args.limit]
    if not selected:
        print(json.dumps({"status": "idle", "pending": 0}, ensure_ascii=False))
        return 0

    validator = Path(args.validator).expanduser()
    agent = Path(args.agent).expanduser()
    approval_file = (
        Path(args.approval_file).expanduser()
        if getattr(args, "approval_file", None)
        else None
    )
    if args.publish and args.limit != 1:
        raise RuntimeError("live publication requires --limit 1 and one exact approval file")
    if args.publish and approval_file is None:
        raise RuntimeError("live publication requires --approval-file")
    completed: set[str] = set()
    results: list[dict[str, Any]] = []
    failures = 0
    for value in selected:
        source = resolve_note_path(value, base)
        try:
            metadata, validation, request_path = prepare(
                source,
                base=base,
                validator=validator,
                state=state,
            )
            result: dict[str, Any] = {
                "file": relative_note_key(source, base),
                "visibility": metadata.visibility,
                "target": metadata.target_name,
                "target_data_source_id": metadata.target_data_source_id,
                "title": metadata.title,
                "attendee_count": len(metadata.attendees),
                "author_name": metadata.author_name,
                "faithful_projection": validation.get("faithful") is True,
                "visual_asset_count": len(metadata.visual_assets),
                "request": str(request_path),
                "action": "prepared",
            }
            if args.preflight or args.publish:
                if args.publish:
                    assert approval_file is not None
                    validate_approval_file(
                        approval_file,
                        request_path,
                        base=base,
                    )
                    assert_metadata_snapshot_fresh(metadata)
                receipt_path = receipt_path_for(source, base)
                receipt = invoke_agent(
                    agent,
                    request_path,
                    receipt_path,
                    publish=args.publish,
                    approval_file=approval_file,
                )
                sanitized_receipt = {
                    key: value
                    for key, value in receipt.items()
                    if key != "fetched_markdown"
                }
                result["receipt"] = sanitized_receipt
                result["action"] = str(receipt.get("status", "unknown"))
                if args.publish and _receipt_success(receipt):
                    validate_receipt_identity(receipt, metadata)
                    assert_metadata_snapshot_fresh(metadata)
                    live_validation = verify_live_receipt(
                        receipt,
                        source=source,
                        candidate=Path(metadata.readable_path),
                        base=base,
                        validator=validator,
                        expected_title=metadata.title,
                        visual_assets=metadata.visual_assets,
                    )
                    result["live_lossless"] = live_validation.get("lossless") is True
                    key = relative_note_key(source, base)
                    previous = state["files"].get(key)
                    history: list[dict[str, Any]] = []
                    if isinstance(previous, dict):
                        old_history = previous.get("previous_publications")
                        if isinstance(old_history, list):
                            history.extend(
                                item for item in old_history if isinstance(item, dict)
                            )
                        if (
                            previous.get("page_id")
                            and previous.get("page_id") != receipt["page_id"]
                        ):
                            history.append(
                                {
                                    "page_id": previous.get("page_id", ""),
                                    "url": previous.get("url", ""),
                                    "visibility": previous.get("visibility", ""),
                                    "target_data_source_id": previous.get(
                                        "target_data_source_id", ""
                                    ),
                                    "cleanup_required": True,
                                }
                            )
                    current_state = {
                        **asdict(metadata),
                        "page_id": receipt["page_id"],
                        "url": receipt.get("url", ""),
                        "post_fetch_verified": True,
                        "published_at": datetime.now().astimezone().isoformat(
                            timespec="seconds"
                        ),
                    }
                    if history:
                        current_state["previous_publications"] = history
                    state["files"][key] = current_state
                    completed.add(value)
                elif args.publish:
                    failures += 1
            results.append(result)
        except Exception as exc:  # keep the queue item for an observable retry
            failures += 1
            results.append(
                {
                    "file": value,
                    "action": "error",
                    "reason": str(exc),
                }
            )

    if args.publish:
        save_json(state_path, state)
        write_pending(pending_file, [value for value in read_pending(pending_file) if value not in completed])
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 1 if failures else 0


def render_files(
    *,
    base: Path,
    files: Iterable[str],
    validator: Path,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for value in files:
        source = resolve_note_path(value, base)
        if not source.is_file():
            raise FileNotFoundError(f"meeting note does not exist: {source}")
        candidate, validation = create_readable_candidate(
            source,
            base=base,
            validator=validator,
        )
        results.append(
            {
                "source": relative_note_key(source, base),
                "readable": relative_note_key(candidate, base),
                "faithful_projection": validation.get("faithful") is True,
                "visual_asset_count": len(visual_assets_for(candidate)),
            }
        )
    return results


def parse_args() -> argparse.Namespace:
    base = repo_base()
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    enqueue_parser = subparsers.add_parser("enqueue")
    enqueue_parser.add_argument("files", nargs="*")
    enqueue_parser.add_argument("--file-list", type=Path)
    enqueue_parser.add_argument("--base", default=str(base))
    enqueue_parser.add_argument(
        "--pending-file",
        default=str(base / "state" / "notion-publication" / "pending.txt"),
    )

    render_parser = subparsers.add_parser("render")
    render_parser.add_argument("files", nargs="*")
    render_parser.add_argument("--file-list", type=Path)
    render_parser.add_argument("--base", default=str(base))
    render_parser.add_argument("--validator", default=str(default_validator()))

    reconcile_parser = subparsers.add_parser("reconcile")
    reconcile_parser.add_argument("--base", default=str(base))
    reconcile_parser.add_argument(
        "--pending-file",
        default=str(base / "state" / "notion-publication" / "pending.txt"),
    )
    reconcile_parser.add_argument(
        "--state",
        default=str(base / "state" / "notion-publication" / "publications.json"),
    )

    approval_parser = subparsers.add_parser("validate-approval")
    approval_parser.add_argument("--base", default=str(base))
    approval_parser.add_argument("--request", type=Path, required=True)
    approval_parser.add_argument("--approval-file", type=Path, required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--base", default=str(base))
    run_parser.add_argument(
        "--pending-file",
        default=str(base / "state" / "notion-publication" / "pending.txt"),
    )
    run_parser.add_argument(
        "--state",
        default=str(base / "state" / "notion-publication" / "publications.json"),
    )
    run_parser.add_argument("--validator", default=str(default_validator()))
    run_parser.add_argument(
        "--agent",
        default=str(base / "sh" / "run-notion-publish-agent.sh"),
    )
    run_parser.add_argument("--limit", type=int, default=1)
    run_parser.add_argument("--only")
    run_parser.add_argument("--visibility", choices=("public", "private"))
    mode = run_parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--publish", action="store_true")
    run_parser.add_argument("--approval-file", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "enqueue":
        base = Path(args.base).resolve()
        values = [*args.files, *file_list(args.file_list)]
        added = enqueue(
            base=base,
            pending_file=Path(args.pending_file).expanduser(),
            files=values,
        )
        print(
            json.dumps(
                {"status": "queued", "added": len(added), "files": added},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "render":
        base = Path(args.base).resolve()
        values = [*args.files, *file_list(args.file_list)]
        results = render_files(
            base=base,
            files=values,
            validator=Path(args.validator).expanduser(),
        )
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    if args.command == "reconcile":
        added = reconcile_changed_labels(
            base=Path(args.base).resolve(),
            pending_file=Path(args.pending_file).expanduser(),
            state_path=Path(args.state).expanduser(),
        )
        print(
            json.dumps(
                {"status": "reconciled", "added": len(added), "files": added},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "validate-approval":
        validate_approval_file(
            args.approval_file.expanduser(),
            args.request.expanduser(),
            base=Path(args.base).resolve(),
        )
        print(json.dumps({"status": "approval-valid"}, ensure_ascii=False))
        return 0
    if args.limit < 1:
        print("--limit must be at least 1", file=sys.stderr)
        return 2
    return run_queue(args)


if __name__ == "__main__":
    raise SystemExit(main())
