#!/usr/bin/env python3
"""Shadow-compare production and overlap-duration speaker assignment.

This experiment imports the production ``assign_speaker`` function unchanged as
the baseline. Candidate results stay under this experiment directory and never
modify production transcripts, notes, audio, or pipeline scripts.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sh"))

from assign_speakers_pyannote import assign_speaker as production_assign_speaker  # noqa: E402


@dataclass(frozen=True)
class Turn:
    start: float
    end: float
    speaker: str


@dataclass(frozen=True)
class WordInterval:
    start: float
    end: float


@dataclass(frozen=True)
class CandidateAssignment:
    speaker: str | None
    uncertain: bool
    simultaneous: bool
    transition: bool
    margin_ratio: float
    overlaps: dict[str, float]


@dataclass(frozen=True)
class _Segment:
    start: float
    end: float


class _AnnotationAdapter:
    """Minimal pyannote-like adapter for the unchanged production baseline."""

    def __init__(self, turns: Iterable[Turn]):
        self.turns = tuple(turns)

    def itertracks(self, *, yield_label: bool = False):
        if not yield_label:
            raise ValueError("the production baseline requires yield_label=True")
        for turn in self.turns:
            yield _Segment(turn.start, turn.end), None, turn.speaker


def _positive_overlap(start: float, end: float, turn: Turn) -> float:
    return max(0.0, min(end, turn.end) - max(start, turn.start))


def _is_simultaneous(word: WordInterval, active: list[Turn]) -> bool:
    for left_index, left in enumerate(active):
        for right in active[left_index + 1 :]:
            if left.speaker == right.speaker:
                continue
            overlap = min(word.end, left.end, right.end) - max(word.start, left.start, right.start)
            if overlap > 0:
                return True
    return False


def _choose_assignment(
    word: WordInterval,
    active: list[Turn],
    *,
    uncertainty_margin: float,
) -> CandidateAssignment:
    duration = word.end - word.start
    if duration <= 1e-9:
        point_hits = [turn for turn in active if turn.start <= word.start <= turn.end]
        speakers = list(dict.fromkeys(turn.speaker for turn in point_hits))
        if not speakers:
            return CandidateAssignment(
                speaker=None,
                uncertain=True,
                simultaneous=False,
                transition=False,
                margin_ratio=0.0,
                overlaps={},
            )
        simultaneous = any(
            left.speaker != right.speaker
            and max(left.start, right.start) < word.start < min(left.end, right.end)
            for left_index, left in enumerate(point_hits)
            for right in point_hits[left_index + 1 :]
        )
        return CandidateAssignment(
            speaker=speakers[0],
            uncertain=True,
            simultaneous=simultaneous,
            transition=len(speakers) > 1,
            margin_ratio=0.0,
            overlaps={speaker: 0.0 for speaker in speakers},
        )

    scores: dict[str, float] = {}
    first_start: dict[str, float] = {}
    for turn in active:
        overlap = _positive_overlap(word.start, word.end, turn)
        if overlap <= 0:
            continue
        scores[turn.speaker] = scores.get(turn.speaker, 0.0) + overlap
        first_start[turn.speaker] = min(first_start.get(turn.speaker, math.inf), turn.start)

    if not scores:
        return CandidateAssignment(
            speaker=None,
            uncertain=True,
            simultaneous=False,
            transition=False,
            margin_ratio=0.0,
            overlaps={},
        )

    # Timestamp arithmetic around an exact boundary can differ by a few ulps
    # (e.g. 0.1 vs 0.10000000000000009). Treat those as a real tie and prefer
    # the earlier turn so a floating-point artifact cannot flip the speaker.
    ranked = sorted(
        scores,
        key=lambda speaker: (-round(scores[speaker], 9), first_start[speaker], speaker),
    )
    best = ranked[0]
    second = scores[ranked[1]] if len(ranked) > 1 else 0.0
    margin_ratio = max(0.0, (scores[best] - second) / duration)
    transition = len(scores) > 1
    simultaneous = _is_simultaneous(word, active)
    uncertain = simultaneous or (transition and margin_ratio < uncertainty_margin)
    return CandidateAssignment(
        speaker=best,
        uncertain=uncertain,
        simultaneous=simultaneous,
        transition=transition,
        margin_ratio=round(margin_ratio, 6),
        overlaps={speaker: round(scores[speaker], 6) for speaker in ranked},
    )


def assign_words_by_overlap(
    words: list[WordInterval],
    turns: list[Turn],
    *,
    uncertainty_margin: float = 0.15,
) -> list[CandidateAssignment]:
    """Assign sorted words in O(words + relevant turns) sweep time."""
    if any(word.end < word.start for word in words):
        raise ValueError("word end must be greater than or equal to start")
    if any(words[index].start < words[index - 1].start for index in range(1, len(words))):
        raise ValueError("words must be sorted by start time")

    ordered_turns = sorted(turns, key=lambda turn: (turn.start, turn.end, turn.speaker))
    results: list[CandidateAssignment] = []
    active: list[Turn] = []
    cursor = 0
    for word in words:
        is_point = word.end - word.start <= 1e-9
        active = [
            turn
            for turn in active
            if (turn.end >= word.start if is_point else turn.end > word.start)
        ]
        while cursor < len(ordered_turns) and (
            ordered_turns[cursor].start <= word.end
            if is_point
            else ordered_turns[cursor].start < word.end
        ):
            turn = ordered_turns[cursor]
            cursor += 1
            if (turn.end >= word.start if is_point else turn.end > word.start):
                active.append(turn)
        results.append(
            _choose_assignment(word, active, uncertainty_margin=uncertainty_margin)
        )
    return results


def assign_word_reference(
    word: WordInterval,
    turns: list[Turn],
    *,
    uncertainty_margin: float = 0.15,
) -> CandidateAssignment:
    """Simple all-turn reference used to verify the sweep implementation."""
    return _choose_assignment(word, turns, uncertainty_margin=uncertainty_margin)


def production_assignments(words: list[WordInterval], turns: list[Turn]) -> list[str | None]:
    annotation = _AnnotationAdapter(turns)
    return [production_assign_speaker(word.start, word.end, annotation) for word in words]


def load_turns(path: Path) -> list[Turn]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        Turn(float(item["start"]), float(item["end"]), str(item["speaker"]))
        for item in payload.get("segments", [])
        if float(item["end"]) >= float(item["start"])
    ]


def load_words(path: Path) -> list[WordInterval]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    words: list[WordInterval] = []
    for segment in payload.get("segments", []):
        for item in segment.get("words") or []:
            if "start" not in item or "end" not in item:
                continue
            words.append(WordInterval(float(item["start"]), float(item["end"])))
    return sorted(words, key=lambda word: (word.start, word.end))


def evaluate_fixtures(path: Path, *, uncertainty_margin: float) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    passed = 0
    for case in payload.get("cases", []):
        word = WordInterval(float(case["word"]["start"]), float(case["word"]["end"]))
        turns = [Turn(float(t["start"]), float(t["end"]), str(t["speaker"])) for t in case["turns"]]
        expected = case["expected"]
        baseline = production_assignments([word], turns)[0]
        candidate = assign_words_by_overlap(
            [word], turns, uncertainty_margin=uncertainty_margin
        )[0]
        ok = (
            candidate.speaker == expected["speaker"]
            and candidate.uncertain is expected["uncertain"]
            and candidate.simultaneous is expected["simultaneous"]
        )
        passed += int(ok)
        rows.append(
            {
                "id": case["id"],
                "baseline_speaker": baseline,
                "candidate": asdict(candidate),
                "expected": expected,
                "passed": ok,
            }
        )
    return {"passed": passed, "total": len(rows), "cases": rows}


def _synthetic_data(duration_seconds: int) -> tuple[list[WordInterval], list[Turn]]:
    turns: list[Turn] = []
    turn_start = 0.0
    turn_index = 0
    while turn_start < duration_seconds:
        turn_end = min(float(duration_seconds), turn_start + 4.0)
        turns.append(Turn(turn_start, turn_end, f"S{turn_index % 4}"))
        if turn_index % 25 == 12:
            turns.append(Turn(turn_start + 2.0, min(turn_end + 1.0, duration_seconds), "S4"))
        turn_start = turn_end
        turn_index += 1
    words = [
        WordInterval(start / 2.0, min(start / 2.0 + 0.42, duration_seconds))
        for start in range(duration_seconds * 2)
    ]
    return words, turns


def benchmark(duration_seconds: int, *, uncertainty_margin: float) -> dict:
    words, turns = _synthetic_data(duration_seconds)

    baseline_start = time.perf_counter()
    baseline = production_assignments(words, turns)
    baseline_seconds = time.perf_counter() - baseline_start

    candidate_start = time.perf_counter()
    candidate = assign_words_by_overlap(
        words, turns, uncertainty_margin=uncertainty_margin
    )
    candidate_seconds = time.perf_counter() - candidate_start

    candidate_speakers = [item.speaker for item in candidate]
    return {
        "duration_seconds": duration_seconds,
        "word_count": len(words),
        "turn_count": len(turns),
        "baseline_seconds": round(baseline_seconds, 6),
        "candidate_seconds": round(candidate_seconds, 6),
        "speedup": round(baseline_seconds / max(candidate_seconds, 1e-9), 2),
        "assignment_changes": sum(a != b for a, b in zip(baseline, candidate_speakers)),
        "candidate_uncertain": sum(item.uncertain for item in candidate),
        "candidate_simultaneous": sum(item.simultaneous for item in candidate),
    }


def compare_real_inputs(
    transcript_json: Path,
    diarization_json: Path,
    *,
    uncertainty_margin: float,
) -> dict:
    words = load_words(transcript_json)
    turns = load_turns(diarization_json)

    baseline_start = time.perf_counter()
    baseline = production_assignments(words, turns)
    baseline_seconds = time.perf_counter() - baseline_start

    candidate_start = time.perf_counter()
    candidate = assign_words_by_overlap(
        words, turns, uncertainty_margin=uncertainty_margin
    )
    candidate_seconds = time.perf_counter() - candidate_start

    changes = []
    for word, before, after in zip(words, baseline, candidate):
        if before == after.speaker:
            continue
        changes.append(
            {
                "start": round(word.start, 3),
                "end": round(word.end, 3),
                "baseline_speaker": before,
                "candidate": asdict(after),
            }
        )

    return {
        "word_count": len(words),
        "turn_count": len(turns),
        "baseline_seconds": round(baseline_seconds, 6),
        "candidate_seconds": round(candidate_seconds, 6),
        "speedup": round(baseline_seconds / max(candidate_seconds, 1e-9), 2),
        "assignment_changes": len(changes),
        "baseline_unmatched": sum(item is None for item in baseline),
        "candidate_uncertain": sum(item.uncertain for item in candidate),
        "candidate_simultaneous": sum(item.simultaneous for item in candidate),
        "unmatched": sum(item.speaker is None for item in candidate),
        "changes": changes[:100],
        "changes_truncated": len(changes) > 100,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=Path(__file__).with_name("fixtures") / "cases.json",
    )
    parser.add_argument("--transcript-json", type=Path)
    parser.add_argument("--diarization-json", type=Path)
    parser.add_argument("--benchmark-seconds", type=int, default=5400)
    parser.add_argument("--uncertainty-margin", type=float, default=0.15)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if (args.transcript_json is None) != (args.diarization_json is None):
        parser.error("--transcript-json and --diarization-json must be provided together")
    return args


def main() -> int:
    args = parse_args()
    report = {
        "experiment": "speaker-assignment-shadow-v1",
        "production_code_modified": False,
        "uncertainty_margin": args.uncertainty_margin,
        "fixtures": evaluate_fixtures(
            args.fixtures, uncertainty_margin=args.uncertainty_margin
        ),
        "synthetic_benchmark": benchmark(
            args.benchmark_seconds, uncertainty_margin=args.uncertainty_margin
        ),
    }
    if args.transcript_json and args.diarization_json:
        report["real_input_comparison"] = compare_real_inputs(
            args.transcript_json,
            args.diarization_json,
            uncertainty_margin=args.uncertainty_margin,
        )

    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["fixtures"]["passed"] == report["fixtures"]["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
