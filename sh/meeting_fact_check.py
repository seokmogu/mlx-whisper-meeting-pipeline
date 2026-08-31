#!/usr/bin/env python3
"""Validate, merge, and render objective meeting fact-check artifacts."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
import os
import re
from pathlib import Path
import tempfile
from urllib.parse import urlparse


SECTION_HEADING = "## 6. 객관 명제 팩트체크"
CHECKABILITIES = {"deterministic", "public_web", "internal_authority", "speaker_conflict"}
WEB_CHECKABILITIES = {"deterministic", "public_web"}
VERDICTS = {
    "verified",
    "contradicted",
    "partially_verified",
    "speaker_conflict",
    "not_assessable",
    "stt_review_needed",
}
EVIDENCE_VERDICTS = VERDICTS - {"speaker_conflict", "stt_review_needed"}
SOURCE_TYPES = {
    "deterministic",
    "official_primary",
    "standard",
    "public_data",
    "company_authority",
    "reliable_secondary",
}
STRONG_SOURCE_TYPES = SOURCE_TYPES - {"reliable_secondary"}
ERROR_TYPES = {
    "none",
    "definition_mismatch",
    "unit_or_calculation",
    "date_or_version",
    "scope_overgeneralization",
    "correlation_causation",
    "outdated_fact",
    "wrong_authority",
    "other",
}
AUDIO_REVIEW_STATUSES = {"confirmed", "not_confirmed", "uncertain"}
SEARCH_HOSTS = {
    "google.com",
    "www.google.com",
    "bing.com",
    "www.bing.com",
    "search.naver.com",
    "duckduckgo.com",
}
VERDICT_LABELS = {
    "verified": "객관 사실과 일치",
    "contradicted": "객관 사실과 충돌",
    "partially_verified": "부분 확인",
    "speaker_conflict": "상충 발언",
    "not_assessable": "판정 불가",
    "stt_review_needed": "STT 확인 필요",
}
ERROR_LABELS = {
    "none": "해당 없음",
    "definition_mismatch": "정의 불일치",
    "unit_or_calculation": "단위·계산 오류",
    "date_or_version": "날짜·버전 불일치",
    "scope_overgeneralization": "적용 범위 과도 일반화",
    "correlation_causation": "상관관계·인과관계 혼동",
    "outdated_fact": "오래된 사실",
    "wrong_authority": "정본·권위 출처 오류",
    "other": "기타",
}
CONFIDENCE_LABELS = {"high": "높음", "medium": "중간", "low": "낮음"}


class FactCheckError(ValueError):
    pass


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FactCheckError(f"JSON root must be an object: {path}")
    return value


def _write_json(path: Path, value: dict) -> None:
    _atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as stream:
            stream.write(text)
            temporary = Path(stream.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _note_source_sha256(path: Path) -> str:
    text = _strip_fact_check(path.read_text(encoding="utf-8"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _strip_fact_check(text: str) -> str:
    text = text.rstrip()
    text = re.sub(
        rf"\n{re.escape(SECTION_HEADING)}\n[\s\S]*\Z",
        "",
        text,
    )
    return text.rstrip() + "\n"


def _require_string(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise FactCheckError(f"{label} must be a{' possibly empty' if allow_empty else ' non-empty'} string")
    return value


def validate_candidates(payload: dict) -> list[str]:
    errors: list[str] = []
    if payload.get("version") != 1:
        errors.append("candidate version must be 1")
    rows = payload.get("candidates")
    if not isinstance(rows, list):
        return [*errors, "candidates must be an array"]
    if len(rows) > 8:
        errors.append("candidates must contain at most 8 items")
    ids: list[str] = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            errors.append(f"candidate {index} must be an object")
            continue
        candidate_id = row.get("id")
        ids.append(str(candidate_id))
        if candidate_id != f"C{index}":
            errors.append("candidate IDs must be contiguous C1..Cn")
        for key in ("speaker", "claim_text", "public_claim", "required_evidence", "importance"):
            if not isinstance(row.get(key), str):
                errors.append(f"{candidate_id}.{key} must be a string")
        for key in ("start_seconds", "end_seconds"):
            value = row.get(key)
            if not isinstance(value, (int, float)) or value < 0:
                errors.append(f"{candidate_id}.{key} must be a non-negative number")
        if isinstance(row.get("start_seconds"), (int, float)) and isinstance(
            row.get("end_seconds"), (int, float)
        ) and row["end_seconds"] < row["start_seconds"]:
            errors.append(f"{candidate_id} end_seconds precedes start_seconds")
        checkability = row.get("checkability")
        if checkability not in CHECKABILITIES:
            errors.append(f"{candidate_id} has invalid checkability")
        if row.get("importance") not in {"high", "medium"}:
            errors.append(f"{candidate_id} has invalid importance")
        if not isinstance(row.get("stt_risk"), bool) or not isinstance(
            row.get("web_search_allowed"), bool
        ):
            errors.append(f"{candidate_id} boolean fields are invalid")
        if row.get("web_search_allowed") and checkability not in WEB_CHECKABILITIES:
            errors.append(f"{candidate_id} cannot use public web search for {checkability}")
        if row.get("web_search_allowed") and not str(row.get("public_claim", "")).strip():
            errors.append(f"{candidate_id} web-search candidate needs a public_claim")
        if checkability == "internal_authority" and not str(row.get("required_evidence", "")).strip():
            errors.append(f"{candidate_id} internal_authority needs required_evidence")
        counterparts = row.get("counterpart_claims")
        if not isinstance(counterparts, list):
            errors.append(f"{candidate_id}.counterpart_claims must be an array")
        elif checkability == "speaker_conflict" and not counterparts:
            errors.append(f"{candidate_id} speaker_conflict needs a counterpart claim")
    if len(ids) != len(set(ids)):
        errors.append("candidate IDs must be unique")
    return errors


def _valid_direct_url(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        return False
    return parsed.netloc.casefold() not in SEARCH_HOSTS


def _contains_korean(value: object) -> bool:
    return isinstance(value, str) and re.search(r"[가-힣]", value) is not None


def validate_evidence(payload: dict, candidate_ids: set[str]) -> list[str]:
    errors: list[str] = []
    if payload.get("version") != 1:
        errors.append("evidence version must be 1")
    if not isinstance(payload.get("web_search_performed"), bool):
        errors.append("web_search_performed must be boolean")
    rows = payload.get("results")
    if not isinstance(rows, list):
        return [*errors, "results must be an array"]
    seen: set[str] = set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            errors.append(f"result {index} must be an object")
            continue
        candidate_id = row.get("candidate_id")
        if candidate_id not in candidate_ids:
            errors.append(f"unknown result candidate: {candidate_id}")
        if candidate_id in seen:
            errors.append(f"duplicate result candidate: {candidate_id}")
        seen.add(str(candidate_id))
        verdict = row.get("verdict")
        if verdict not in EVIDENCE_VERDICTS:
            errors.append(f"{candidate_id} has invalid evidence verdict")
        if row.get("error_type") not in ERROR_TYPES:
            errors.append(f"{candidate_id} has invalid error_type")
        if row.get("confidence") not in {"high", "medium", "low"}:
            errors.append(f"{candidate_id} has invalid confidence")
        for key in ("corrected_fact", "explanation", "valid_as_of"):
            if not isinstance(row.get(key), str):
                errors.append(f"{candidate_id}.{key} must be a string")
        if not _contains_korean(row.get("explanation")):
            errors.append(f"{candidate_id}.explanation must be written in Korean")
        if str(row.get("corrected_fact", "")).strip() and not _contains_korean(
            row.get("corrected_fact")
        ):
            errors.append(f"{candidate_id}.corrected_fact must be written in Korean")
        sources = row.get("sources")
        if not isinstance(sources, list):
            errors.append(f"{candidate_id}.sources must be an array")
            continue
        source_hosts: set[str] = set()
        strong = False
        for source_index, source in enumerate(sources, 1):
            if not isinstance(source, dict):
                errors.append(f"{candidate_id} source {source_index} must be an object")
                continue
            source_type = source.get("source_type")
            if source_type not in SOURCE_TYPES:
                errors.append(f"{candidate_id} source {source_index} has invalid source_type")
            url = str(source.get("url", ""))
            if source_type == "deterministic":
                if not str(source.get("locator", "")).strip():
                    errors.append(f"{candidate_id} deterministic source needs evidence")
            elif not _valid_direct_url(url):
                errors.append(f"{candidate_id} source must use a direct HTTPS URL")
            if url:
                source_hosts.add(urlparse(url).netloc.casefold())
            if source_type in STRONG_SOURCE_TYPES:
                strong = True
            for key in ("title", "locator", "why_relevant"):
                if not isinstance(source.get(key), str) or not source[key].strip():
                    errors.append(f"{candidate_id} source {source_index}.{key} is required")
            if not _contains_korean(source.get("why_relevant")):
                errors.append(f"{candidate_id} source {source_index}.why_relevant must be written in Korean")
        if verdict in {"verified", "contradicted", "partially_verified"} and not sources:
            errors.append(f"{candidate_id} verdict requires sources")
        if verdict == "contradicted":
            if row.get("confidence") != "high":
                errors.append(f"{candidate_id} contradicted requires high confidence")
            if not str(row.get("corrected_fact", "")).strip():
                errors.append(f"{candidate_id} contradicted requires corrected_fact")
            if not strong and len(source_hosts) < 2:
                errors.append(f"{candidate_id} contradicted needs a primary source or two independent sources")
    return errors


def validate_audio_review(payload: dict, candidate_ids: set[str]) -> list[str]:
    errors: list[str] = []
    if payload.get("version") != 1:
        errors.append("audio review version must be 1")
    rows = payload.get("results")
    if not isinstance(rows, list):
        return [*errors, "audio review results must be an array"]
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            errors.append("audio review result must be an object")
            continue
        candidate_id = row.get("candidate_id")
        if candidate_id not in candidate_ids:
            errors.append(f"unknown audio review candidate: {candidate_id}")
        if candidate_id in seen:
            errors.append(f"duplicate audio review candidate: {candidate_id}")
        seen.add(str(candidate_id))
        if row.get("status") not in AUDIO_REVIEW_STATUSES:
            errors.append(f"{candidate_id} has invalid audio review status")
        if not _contains_korean(row.get("explanation")):
            errors.append(f"{candidate_id} audio review explanation must be Korean")
        if not isinstance(row.get("supporting_text"), str):
            errors.append(f"{candidate_id} supporting_text must be a string")
    return errors


def prepare_search(candidates: dict) -> dict:
    errors = validate_candidates(candidates)
    if errors:
        raise FactCheckError("; ".join(errors))
    rows = []
    for row in candidates["candidates"]:
        if row["web_search_allowed"] and row["checkability"] in WEB_CHECKABILITIES:
            rows.append(
                {
                    "candidate_id": row["id"],
                    "public_claim": row["public_claim"],
                    "checkability": row["checkability"],
                    "importance": row["importance"],
                    "stt_risk": row["stt_risk"],
                }
            )
    return {"version": 1, "candidates": rows}


def _not_assessable(candidate: dict, explanation: str) -> dict:
    return {
        "id": candidate["id"],
        "speaker": candidate["speaker"],
        "start_seconds": candidate["start_seconds"],
        "end_seconds": candidate["end_seconds"],
        "claim_text": candidate["claim_text"],
        "verdict": "not_assessable",
        "corrected_fact": "",
        "explanation": explanation,
        "error_type": "none",
        "confidence": "low",
        "valid_as_of": "",
        "sources": [],
        "counterpart_claims": candidate["counterpart_claims"],
        "stt_risk": candidate["stt_risk"],
        "required_evidence": candidate.get("required_evidence", ""),
    }


def merge_results(
    candidates: dict,
    evidence: dict,
    *,
    note: Path,
    transcript: Path,
    web_search_observed: bool,
    audio_review: dict | None = None,
) -> dict:
    candidate_errors = validate_candidates(candidates)
    search_ids = {
        row["id"]
        for row in candidates.get("candidates", [])
        if row.get("web_search_allowed") and row.get("checkability") in WEB_CHECKABILITIES
    }
    evidence_errors = validate_evidence(evidence, search_ids)
    audio_review = audio_review or {"version": 1, "results": []}
    audio_errors = validate_audio_review(audio_review, {row["id"] for row in candidates.get("candidates", [])})
    errors = [*candidate_errors, *evidence_errors, *audio_errors]
    if errors:
        raise FactCheckError("; ".join(errors))
    evidence_by_id = {row["candidate_id"]: row for row in evidence["results"]}
    audio_by_id = {row["candidate_id"]: row for row in audio_review["results"]}
    rows: list[dict] = []
    for candidate in candidates["candidates"]:
        if candidate["checkability"] == "speaker_conflict":
            rows.append(
                {
                    "id": candidate["id"],
                    "speaker": candidate["speaker"],
                    "start_seconds": candidate["start_seconds"],
                    "end_seconds": candidate["end_seconds"],
                    "claim_text": candidate["claim_text"],
                    "verdict": "speaker_conflict",
                    "corrected_fact": "",
                    "explanation": "상충하는 발언이 있으나 공개된 객관 기준만으로 어느 쪽이 맞는지 정할 수 없다.",
                    "error_type": "none",
                    "confidence": "low",
                    "valid_as_of": "",
                    "sources": [],
                    "counterpart_claims": candidate["counterpart_claims"],
                    "stt_risk": candidate["stt_risk"],
                    "required_evidence": candidate.get("required_evidence", ""),
                }
            )
            continue
        if candidate["checkability"] == "internal_authority":
            rows.append(
                _not_assessable(candidate, "사내 정본이 필요한 명제로 공개 웹검색 대상에서 제외했다.")
            )
            continue
        if not web_search_observed or not evidence.get("web_search_performed"):
            rows.append(_not_assessable(candidate, "실제 웹검색 실행 증거가 없어 외부 검증을 확정하지 않았다."))
            continue
        result = evidence_by_id.get(candidate["id"])
        if result is None:
            rows.append(_not_assessable(candidate, "웹검색 결과에서 이 명제의 검증 결과를 찾지 못했다."))
            continue
        merged = {
                "id": candidate["id"],
                "speaker": candidate["speaker"],
                "start_seconds": candidate["start_seconds"],
                "end_seconds": candidate["end_seconds"],
                "claim_text": candidate["claim_text"],
                **{key: result[key] for key in ("verdict", "corrected_fact", "explanation", "error_type", "confidence", "valid_as_of", "sources")},
                "counterpart_claims": candidate["counterpart_claims"],
                "stt_risk": candidate["stt_risk"],
                "required_evidence": candidate.get("required_evidence", ""),
                "audio_review": audio_by_id.get(candidate["id"]),
            }
        audio_status = (merged.get("audio_review") or {}).get("status")
        if candidate["stt_risk"] and audio_status != "confirmed" and merged["verdict"] in {
            "verified",
            "contradicted",
            "partially_verified",
        }:
            merged["verdict"] = "stt_review_needed"
            merged["explanation"] = (
                "외부 근거와 비교 결과는 확보했지만 전사 정확도가 불확실하다. "
                "원음에서 명제가 정확히 발화됐는지 확인한 뒤 최종 판정해야 한다. "
                + merged["explanation"]
            )
            merged["confidence"] = "low"
        rows.append(merged)
    counts = Counter(row["verdict"] for row in rows)
    return {
        "version": 1,
        "status": "completed" if rows else "no_checkable_claims",
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "note_path": str(note.resolve()),
        "note_sha256": _note_source_sha256(note),
        "transcript_path": str(transcript.resolve()),
        "transcript_sha256": _sha256(transcript),
        "web_search_observed": web_search_observed,
        "audio_review_performed": bool(audio_review["results"]),
        "summary": {key: counts.get(key, 0) for key in sorted(VERDICTS)},
        "items": rows,
    }


def _timestamp(seconds: float) -> str:
    whole = max(0, int(seconds))
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _one_line(value: str) -> str:
    return " ".join(value.split()).replace('"', "”")


def _markdown_link_text(value: str) -> str:
    return _one_line(value).replace("[", "(").replace("]", ")")


def render_markdown(result: dict) -> str:
    items = result.get("items", [])
    counts = Counter(item.get("verdict") for item in items)
    summary_parts = [
        f"{VERDICT_LABELS[key]} {counts[key]}건"
        for key in VERDICT_LABELS
        if counts.get(key)
    ]
    lines = [SECTION_HEADING, "", "- 범위: 객관적으로 판정 가능한 명제와 상충 발언만 검토했다. 의견·제안·예측은 판정하지 않았다."]
    lines.append(f"- 웹검색: {'실제 실행 확인' if result.get('web_search_observed') else '실행하지 않았거나 확인 불가'}")
    lines.append(f"- 결과: {' · '.join(summary_parts) if summary_parts else '판정 대상 없음'}")
    if result.get("failure_reason"):
        lines.append(f"- 확인 필요: {_one_line(result['failure_reason'])}")
    if not items:
        lines.extend(["", "- 객관적으로 판정할 고위험 명제나 상충 발언을 찾지 못했거나 검증을 수행할 수 없었다."])
        return "\n".join(lines).rstrip() + "\n"

    compact_internal = [
        item
        for item in items
        if item.get("verdict") == "not_assessable"
        and str(item.get("explanation", "")).startswith("사내 정본")
    ]
    display_items = [item for item in items if item not in compact_internal]
    if compact_internal:
        lines.extend(["", "### 추가 확인이 필요한 명제"])
        for item in compact_internal:
            lines.append(f"- **사내 정본 필요 · {_one_line(item['claim_text'])}**")
            lines.append(f"  판정하지 못한 이유: {_one_line(item['explanation'])}")
            required = _one_line(item.get("required_evidence", "")) or "확인할 사내 정본 지정 필요"
            lines.append(f"  확인할 정본: {required}")
    if not display_items:
        lines.extend(["", "- 공개 근거로 판정할 항목이나 객관적으로 정리할 상충 발언이 없었다."])
        return "\n".join(lines).rstrip() + "\n"

    for index, item in enumerate(display_items, 1):
        verdict = item["verdict"]
        lines.extend(
            [
                "",
                f"### F{index}. {VERDICT_LABELS[verdict]}",
                f"- 발언: “{_one_line(item['claim_text'])}”",
                f"- 위치: 화자 {item['speaker']} · {_timestamp(item['start_seconds'])}",
            ]
        )
        for counterpart in item.get("counterpart_claims", []):
            lines.append(
                f"- 상충 발언: “{_one_line(counterpart['claim_text'])}” · 화자 {counterpart['speaker']} · {_timestamp(counterpart['start_seconds'])}"
            )
        if item.get("corrected_fact"):
            lines.append(f"- 바로잡기: {_one_line(item['corrected_fact'])}")
        label = "틀린 이유" if verdict == "contradicted" else "판정 이유"
        lines.append(f"- {label}: {_one_line(item['explanation'])}")
        if item.get("error_type") and item["error_type"] != "none":
            lines.append(f"- 오류 유형: {ERROR_LABELS[item['error_type']]}")
        for source in item.get("sources", []):
            if source.get("url"):
                source_link = f"[{_markdown_link_text(source['title'])}]({source['url']})"
            else:
                source_link = _one_line(source["title"])
            lines.append(
                f"- 근거: {source_link} — {_one_line(source['locator'])}; {_one_line(source['why_relevant'])}"
            )
        if item.get("valid_as_of"):
            lines.append(f"- 기준 시점: {_one_line(item['valid_as_of'])}")
        lines.append(f"- 확신도: {CONFIDENCE_LABELS[item['confidence']]}")
        if item.get("stt_risk"):
            review = item.get("audio_review") or {}
            if review:
                status_label = {
                    "confirmed": "구간 재전사에서 명제 확인",
                    "not_confirmed": "구간 재전사에서 명제 미확인",
                    "uncertain": "구간 재전사로도 불명확",
                }.get(review.get("status"), "재검증 상태 확인 필요")
                lines.append(f"- 오디오 재검증: {status_label} — {_one_line(review.get('explanation', ''))}")
                if review.get("supporting_text"):
                    lines.append(f"- 구간 재전사: “{_one_line(review['supporting_text'])}”")
            if item["verdict"] == "stt_review_needed":
                lines.append("- STT 확인: 원음 재청취 필요")
    return "\n".join(lines).rstrip() + "\n"


def apply_to_note(note: Path, result: dict) -> None:
    text = _strip_fact_check(note.read_text(encoding="utf-8")).rstrip()
    _atomic_write_text(note, text + "\n\n" + render_markdown(result))


def fallback_result(note: Path, transcript: Path, reason: str) -> dict:
    return {
        "version": 1,
        "status": "not_assessable",
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "note_path": str(note.resolve()),
        "note_sha256": _note_source_sha256(note),
        "transcript_path": str(transcript.resolve()),
        "transcript_sha256": _sha256(transcript),
        "web_search_observed": False,
        "failure_reason": reason,
        "summary": {key: 0 for key in sorted(VERDICTS)},
        "items": [],
    }


def _fail(errors: list[str]) -> int:
    for error in errors:
        print(f"invalid objective fact check: {error}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_candidates_parser = subparsers.add_parser("validate-candidates")
    validate_candidates_parser.add_argument("input", type=Path)

    prepare_parser = subparsers.add_parser("prepare-search")
    prepare_parser.add_argument("input", type=Path)
    prepare_parser.add_argument("output", type=Path)

    validate_evidence_parser = subparsers.add_parser("validate-evidence")
    validate_evidence_parser.add_argument("input", type=Path)
    validate_evidence_parser.add_argument("candidates", type=Path)

    validate_audio_parser = subparsers.add_parser("validate-audio-review")
    validate_audio_parser.add_argument("input", type=Path)
    validate_audio_parser.add_argument("candidates", type=Path)

    merge_parser = subparsers.add_parser("merge")
    merge_parser.add_argument("--candidates", type=Path, required=True)
    merge_parser.add_argument("--evidence", type=Path, required=True)
    merge_parser.add_argument("--note", type=Path, required=True)
    merge_parser.add_argument("--transcript", type=Path, required=True)
    merge_parser.add_argument("--output", type=Path, required=True)
    merge_parser.add_argument("--web-search-observed", action="store_true")
    merge_parser.add_argument("--audio-review", type=Path)

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--result", type=Path, required=True)
    apply_parser.add_argument("--note", type=Path, required=True)

    fallback_parser = subparsers.add_parser("fallback")
    fallback_parser.add_argument("--note", type=Path, required=True)
    fallback_parser.add_argument("--transcript", type=Path, required=True)
    fallback_parser.add_argument("--output", type=Path, required=True)
    fallback_parser.add_argument("--reason", required=True)

    strip_parser = subparsers.add_parser("strip-note")
    strip_parser.add_argument("--input", type=Path, required=True)
    strip_parser.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    try:
        if args.command == "validate-candidates":
            errors = validate_candidates(_read_json(args.input))
            if errors:
                return _fail(errors)
            print("objective fact-check candidates: PASS")
            return 0
        if args.command == "prepare-search":
            _write_json(args.output, prepare_search(_read_json(args.input)))
            return 0
        if args.command == "validate-evidence":
            candidates = prepare_search(_read_json(args.candidates))
            ids = {row["candidate_id"] for row in candidates["candidates"]}
            errors = validate_evidence(_read_json(args.input), ids)
            if errors:
                return _fail(errors)
            print("objective fact-check evidence: PASS")
            return 0
        if args.command == "validate-audio-review":
            candidates = _read_json(args.candidates)
            ids = {row["id"] for row in candidates.get("candidates", [])}
            errors = validate_audio_review(_read_json(args.input), ids)
            if errors:
                return _fail(errors)
            print("objective fact-check audio review: PASS")
            return 0
        if args.command == "merge":
            result = merge_results(
                _read_json(args.candidates),
                _read_json(args.evidence),
                note=args.note,
                transcript=args.transcript,
                web_search_observed=args.web_search_observed,
                audio_review=_read_json(args.audio_review) if args.audio_review else None,
            )
            _write_json(args.output, result)
            return 0
        if args.command == "apply":
            apply_to_note(args.note, _read_json(args.result))
            return 0
        if args.command == "fallback":
            result = fallback_result(args.note, args.transcript, args.reason)
            _write_json(args.output, result)
            apply_to_note(args.note, result)
            return 0
        if args.command == "strip-note":
            _atomic_write_text(args.output, _strip_fact_check(args.input.read_text(encoding="utf-8")))
            return 0
    except (FactCheckError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"objective fact check failed: {exc}")
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
