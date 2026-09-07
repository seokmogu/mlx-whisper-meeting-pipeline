#!/usr/bin/env python3
"""Offline, gold-reference transcript quality evaluation.

The report deliberately contains metrics, counts, and opaque hashes only. It
never copies transcripts, paths, critical phrases, reviewer names, or labels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePath
import re
import sys
import unicodedata

from phonetic_name_candidates import _edit_distance


CASE_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
TIME_TOKEN = r"(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d+)?"
BRACKETED_TIMESTAMP_PREFIX_RE = re.compile(
    rf"^\s*\[\s*{TIME_TOKEN}(?:\s*-\s*{TIME_TOKEN})?\s*\]\s*"
)
UNBRACKETED_TIMESTAMP_PREFIX_RE = re.compile(rf"^\s*{TIME_TOKEN}(?=\s+)")
SPEAKER_PREFIX_RE = re.compile(
    r"^\s*(?:(?:화자|(?i:speaker))\s*(?:[A-Za-z0-9가-힣_-]{1,32}|\?)\s*[:：]|(?:[A-Z]|S[1-9]\d*|\?)\s*[:：])\s*"
)
CRITICAL_CATEGORIES = ("numbers", "negation", "owner", "deadline")


class ManifestError(ValueError):
    pass


def normalize_text(value: str) -> str:
    """NFC-normalize text and strip only line-leading transport prefixes."""
    lines: list[str] = []
    for line in unicodedata.normalize("NFC", value).splitlines():
        without_timestamp = BRACKETED_TIMESTAMP_PREFIX_RE.sub("", line, count=1)
        if without_timestamp == line:
            candidate = UNBRACKETED_TIMESTAMP_PREFIX_RE.sub("", line, count=1)
            if SPEAKER_PREFIX_RE.match(candidate):
                without_timestamp = candidate
        without_speaker = SPEAKER_PREFIX_RE.sub("", without_timestamp, count=1)
        if without_speaker.strip():
            lines.append(" ".join(without_speaker.split()))
    return " ".join(lines)


def cer_units(value: str) -> list[str]:
    return list("".join(value.split()))


def wer_units(value: str) -> list[str]:
    return value.split()


def metric(reference: list[str], hypothesis: list[str]) -> dict[str, int | float]:
    distance = _edit_distance(reference, hypothesis)
    return {
        "edit_distance": distance,
        "reference_units": len(reference),
        "hypothesis_units": len(hypothesis),
        "rate": distance / len(reference),
    }


def phrase_tokens(value: str) -> tuple[str, ...]:
    return tuple(normalize_text(value).split())


def contains_token_phrase(tokens: list[str], phrase: tuple[str, ...]) -> bool:
    """Match complete, contiguous whitespace-delimited lexical tokens only."""
    width = len(phrase)
    return bool(phrase) and any(tuple(tokens[index:index + width]) == phrase for index in range(len(tokens) - width + 1))


def opaque_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def resolve_relative(base: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{field} must be a nonblank relative path")
    raw = PurePath(value)
    if raw.is_absolute() or ".." in raw.parts:
        raise ManifestError(f"{field} must stay below the manifest directory")
    resolved = (base / raw).resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ManifestError(f"{field} must stay below the manifest directory") from exc
    return resolved


def paths_alias(first: Path, second: Path) -> bool:
    if first.resolve() == second.resolve():
        return True
    try:
        return os.path.samefile(first, second)
    except FileNotFoundError:
        return False


def read_text_bytes(path: Path, field: str) -> tuple[bytes, str]:
    if not path.is_file():
        raise ManifestError(f"{field} must name an existing regular file")
    try:
        raw = path.read_bytes()
        return raw, raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ManifestError(f"{field} must be readable UTF-8 text") from exc


def read_nonempty_reference(path: Path, field: str) -> tuple[bytes, str]:
    raw, text = read_text_bytes(path, field)
    if not text.strip():
        raise ManifestError(f"{field} must not be empty")
    normalized = normalize_text(text)
    if not normalized:
        raise ManifestError(f"{field} must remain nonempty after transport-prefix normalization")
    return raw, normalized


def read_hypothesis(path: Path, field: str) -> tuple[bytes, str]:
    raw, text = read_text_bytes(path, field)
    return raw, normalize_text(text)


def validate_thresholds(manifest: dict) -> dict[str, float]:
    thresholds = manifest.get("thresholds")
    if thresholds is None:
        return {}
    if not isinstance(thresholds, dict):
        raise ManifestError("thresholds must be an object")
    result: dict[str, float] = {}
    for key, value in thresholds.items():
        if key not in {"cer", "wer"}:
            raise ManifestError("thresholds may contain only cer and wer")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ManifestError(f"thresholds.{key} must be a nonnegative number")
        result[key] = float(value)
    return result


def review_metadata(case: dict, index: int) -> dict[str, bool]:
    reviewed = case.get("reviewed", False)
    if not isinstance(reviewed, bool):
        raise ManifestError(f"cases[{index}].reviewed must be true or false")
    reviewer = case.get("reviewer", "")
    evidence = case.get("evidence", "")
    if not isinstance(reviewer, str) or not isinstance(evidence, str):
        raise ManifestError(f"cases[{index}] review metadata must be strings")
    return {
        "reviewed_flag": reviewed,
        "reviewer_present": bool(reviewer.strip()),
        "evidence_present": bool(evidence.strip()),
    }


def parse_critical(case: dict, reference_tokens: list[str], index: int) -> tuple[dict[str, int | str], ...]:
    critical = case.get("critical", {})
    if not isinstance(critical, dict):
        raise ManifestError(f"cases[{index}].critical must be an object")
    unknown = set(critical) - set(CRITICAL_CATEGORIES)
    if unknown:
        raise ManifestError(f"cases[{index}].critical has an unsupported category")
    checks: list[dict[str, int | str]] = []
    for category in CRITICAL_CATEGORIES:
        values = critical.get(category, [])
        if not isinstance(values, list):
            raise ManifestError(f"cases[{index}].critical.{category} must be a list")
        phrases: list[tuple[str, ...]] = []
        for phrase_index, value in enumerate(values):
            if not isinstance(value, str) or not value.strip():
                raise ManifestError(f"cases[{index}].critical.{category}[{phrase_index}] must be nonblank")
            tokens = phrase_tokens(value)
            if not tokens:
                raise ManifestError(f"cases[{index}].critical.{category}[{phrase_index}] must normalize to tokens")
            if not contains_token_phrase(reference_tokens, tokens):
                raise ManifestError(f"cases[{index}].critical.{category}[{phrase_index}] is absent from its reference")
            phrases.append(tokens)
        if phrases:
            checks.append({"category": category, "reference_phrase_count": len(phrases), "lexically_present_count": 0, "lexically_missing_count": 0})
    return tuple(checks)


def critical_report(case: dict, reference_tokens: list[str], hypothesis_tokens: list[str], index: int) -> dict:
    checks = parse_critical(case, reference_tokens, index)
    critical = case.get("critical", {})
    rows: list[dict] = []
    for row in checks:
        category = str(row["category"])
        phrases = [phrase_tokens(value) for value in critical[category]]
        present = sum(contains_token_phrase(hypothesis_tokens, phrase) for phrase in phrases)
        rows.append(
            {
                "category": category,
                "reference_phrase_count": len(phrases),
                "lexically_present_count": present,
                "lexically_missing_count": len(phrases) - present,
            }
        )
    return {"mode": "lexical_token_phrase_presence_only", "categories": rows}


def speaker_report(case: dict, index: int) -> dict:
    supplied = case.get("speaker_labels")
    if supplied is None:
        return {"status": "not_evaluated", "pair_count": 0, "matching_count": None, "mismatched_count": None}
    if not isinstance(supplied, list) or not supplied:
        raise ManifestError(f"cases[{index}].speaker_labels must be a nonempty list when supplied")
    matching = 0
    for pair_index, pair in enumerate(supplied):
        if not isinstance(pair, dict):
            raise ManifestError(f"cases[{index}].speaker_labels[{pair_index}] must be an object")
        reference = pair.get("reference")
        hypothesis = pair.get("hypothesis")
        if not isinstance(reference, str) or not reference.strip() or not isinstance(hypothesis, str) or not hypothesis.strip():
            raise ManifestError(f"cases[{index}].speaker_labels[{pair_index}] requires nonblank labels")
        matching += unicodedata.normalize("NFC", reference).strip() == unicodedata.normalize("NFC", hypothesis).strip()
    return {
        "status": "evaluated",
        "pair_count": len(supplied),
        "matching_count": matching,
        "mismatched_count": len(supplied) - matching,
    }


def case_verdict(review: dict[str, bool], thresholds: dict[str, float], metrics: dict, critical: dict, speakers: dict) -> str:
    if not (review["reviewed_flag"] and review["reviewer_present"] and review["evidence_present"]):
        return "not_reviewed"
    if not thresholds:
        return "measured_not_gated"
    threshold_failed = any(metrics[name]["rate"] > threshold for name, threshold in thresholds.items())
    lexical_missing = any(row["lexically_missing_count"] for row in critical["categories"])
    speaker_failed = speakers["status"] == "evaluated" and speakers["mismatched_count"] > 0
    return "gated_fail" if threshold_failed or lexical_missing or speaker_failed else "gated_pass"


def evaluate(manifest_path: Path, output_path: Path) -> dict:
    if paths_alias(manifest_path, output_path):
        raise ManifestError("output must not alias the manifest")
    try:
        raw_manifest = manifest_path.read_bytes()
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError("manifest must be readable UTF-8 JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ManifestError("manifest.version must be 1")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ManifestError("manifest.cases must be a nonempty list")
    thresholds = validate_thresholds(manifest)
    base = manifest_path.parent.resolve()
    seen_ids: set[str] = set()
    reference_sources: list[Path] = []
    hypothesis_sources: list[Path] = []
    by_case: dict[str, dict] = {}
    aggregate_cer_distance = aggregate_cer_reference = aggregate_cer_hypothesis = 0
    aggregate_wer_distance = aggregate_wer_reference = aggregate_wer_hypothesis = 0
    evaluated_count = 0
    not_evaluated_count = 0

    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ManifestError(f"cases[{index}] must be an object")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not CASE_ID_RE.fullmatch(case_id):
            raise ManifestError(f"cases[{index}].id must be an opaque lowercase slug")
        if case_id in seen_ids:
            raise ManifestError(f"cases[{index}].id duplicates an earlier case")
        seen_ids.add(case_id)
        report_id = opaque_hash(case_id)
        review = review_metadata(case, index)

        reference_value = case.get("reference")
        hypothesis_value = case.get("hypothesis")
        reference_path = None
        hypothesis_path = None
        if reference_value is not None:
            reference_path = resolve_relative(base, reference_value, f"cases[{index}].reference")
            if paths_alias(reference_path, output_path):
                raise ManifestError(f"output must not alias cases[{index}].reference")
            if any(paths_alias(reference_path, prior) for prior in hypothesis_sources):
                raise ManifestError(f"cases[{index}].reference must not alias a hypothesis source")
            reference_sources.append(reference_path)
            if hypothesis_value is None:
                raise ManifestError(f"cases[{index}].hypothesis is required when reference is supplied")
            hypothesis_path = resolve_relative(base, hypothesis_value, f"cases[{index}].hypothesis")
            if paths_alias(hypothesis_path, output_path):
                raise ManifestError(f"output must not alias cases[{index}].hypothesis")
            if any(paths_alias(hypothesis_path, prior) for prior in reference_sources):
                raise ManifestError(f"cases[{index}].hypothesis must not alias a reference source")
            hypothesis_sources.append(hypothesis_path)
        elif hypothesis_value is not None:
            hypothesis_path = resolve_relative(base, hypothesis_value, f"cases[{index}].hypothesis")
            if paths_alias(hypothesis_path, output_path):
                raise ManifestError(f"output must not alias cases[{index}].hypothesis")
            if any(paths_alias(hypothesis_path, prior) for prior in reference_sources):
                raise ManifestError(f"cases[{index}].hypothesis must not alias a reference source")
            hypothesis_sources.append(hypothesis_path)

        if reference_path is None:
            not_evaluated_count += 1
            by_case[report_id] = {
                "evaluation_status": "not_evaluated",
                "review_metadata": review,
                "quality_verdict": "not_evaluated",
            }
            continue

        reference_bytes, reference = read_nonempty_reference(reference_path, f"cases[{index}].reference")
        hypothesis_bytes, hypothesis = read_hypothesis(hypothesis_path, f"cases[{index}].hypothesis")
        reference_cer, hypothesis_cer = cer_units(reference), cer_units(hypothesis)
        reference_wer, hypothesis_wer = wer_units(reference), wer_units(hypothesis)
        metrics = {"cer": metric(reference_cer, hypothesis_cer), "wer": metric(reference_wer, hypothesis_wer)}
        critical = critical_report(case, reference_wer, hypothesis_wer, index)
        speakers = speaker_report(case, index)
        by_case[report_id] = {
            "evaluation_status": "evaluated",
            "review_metadata": review,
            "source_sha256": {
                "reference": hashlib.sha256(reference_bytes).hexdigest(),
                "hypothesis": hashlib.sha256(hypothesis_bytes).hexdigest(),
            },
            "metrics": metrics,
            "critical_expression_check": critical,
            "speaker_label_check": speakers,
            "quality_verdict": case_verdict(review, thresholds, metrics, critical, speakers),
        }
        evaluated_count += 1
        aggregate_cer_distance += int(metrics["cer"]["edit_distance"])
        aggregate_cer_reference += int(metrics["cer"]["reference_units"])
        aggregate_cer_hypothesis += int(metrics["cer"]["hypothesis_units"])
        aggregate_wer_distance += int(metrics["wer"]["edit_distance"])
        aggregate_wer_reference += int(metrics["wer"]["reference_units"])
        aggregate_wer_hypothesis += int(metrics["wer"]["hypothesis_units"])

    def aggregate(distance: int, reference_units: int, hypothesis_units: int) -> dict[str, int | float | None]:
        return {
            "edit_distance": distance,
            "reference_units": reference_units,
            "hypothesis_units": hypothesis_units,
            "rate": distance / reference_units if reference_units else None,
        }

    return {
        "schema_version": 1,
        "manifest_sha256": hashlib.sha256(raw_manifest).hexdigest(),
        "thresholds_configured": bool(thresholds),
        "evaluated_case_count": evaluated_count,
        "not_evaluated_case_count": not_evaluated_count,
        "gated_fail_case_count": sum(
            item.get("quality_verdict") == "gated_fail" for item in by_case.values()
        ),
        "aggregate": {
            "cer": aggregate(aggregate_cer_distance, aggregate_cer_reference, aggregate_cer_hypothesis),
            "wer": aggregate(aggregate_wer_distance, aggregate_wer_reference, aggregate_wer_hypothesis),
        },
        "by_case_id_sha256": by_case,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline gold-reference transcript quality evaluator")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = evaluate(args.manifest.resolve(), args.output.resolve())
    except ManifestError as exc:
        print(f"invalid quality-evaluation manifest: {exc}", file=sys.stderr)
        return 2
    except (OSError, UnicodeError):
        print("invalid quality-evaluation manifest: input could not be read", file=sys.stderr)
        return 2
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        print("could not write quality-evaluation report", file=sys.stderr)
        return 2
    print(f"transcript quality evaluation: {report['evaluated_case_count']} evaluated, {report['not_evaluated_case_count']} not evaluated")
    return 1 if report["gated_fail_case_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
