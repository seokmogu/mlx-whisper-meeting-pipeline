# Offline transcript-quality evaluation

Run the evaluator only with human-reviewed gold references. It reads the manifest and the explicitly listed relative text files; it does not call models, audio tools, cloud services, or inspect directories.

```bash
python3 sh/evaluate_transcript_quality.py path/to/manifest.json --output path/to/report.json
```

`docs/transcript-quality.example.json` is synthetic. To make a runnable copy, create the two synthetic files it names, for example:

```bash
QUALITY_TMP="$(mktemp -d)"
mkdir -p "$QUALITY_TMP/synthetic"
printf '담당자 김가상은 10건을 금요일까지 처리합니다. 안 됩니다.\n' > "$QUALITY_TMP/synthetic/gold-case-001.txt"
printf '담당자 김가상은 10건을 금요일까지 처리합니다. 안 됩니다.\n' > "$QUALITY_TMP/synthetic/hypothesis-case-001.txt"
cp docs/transcript-quality.example.json "$QUALITY_TMP/manifest.json"
python3 sh/evaluate_transcript_quality.py "$QUALITY_TMP/manifest.json" --output "$QUALITY_TMP/report.json"
```

The manifest uses `version: 1` and opaque lowercase-slug case IDs. A case with no `reference` is reported as `not_evaluated`; the evaluator never creates gold text from a model result. A supplied reference must be nonempty; its hypothesis file may be empty. Both paths must be relative and below the manifest directory. `reviewed: true` plus nonblank `reviewer` and `evidence` is an operator assertion required before the evaluator can issue a gated quality verdict; it is not independently verified human-review evidence. The example thresholds are demonstration values, not production acceptance criteria.

CER removes whitespace after NFC and transport-prefix normalization. WER uses whitespace-delimited words. At each line start, bracketed timestamps and actual transport speaker labels are stripped separately, including the local format `[00:01:02 - 00:01:05] A:`. Explicit `화자`/`speaker` labels and `A`–`Z` or `S27`-style labels qualify; business labels such as `결정:` remain content. An unbracketed time is stripped only when followed by a transport speaker label, so `10:00에 출발` remains content. An empty hypothesis is valid and counts as deletions. Rates use `edit_distance / reference_units`, so insertion-heavy hypotheses can exceed 1.

Critical checks accept only the `numbers`, `negation`, `owner`, and `deadline` categories. Each supplied phrase must occur in its reference and is checked as a complete, contiguous whitespace-delimited token phrase in the hypothesis, including its exact Korean particle and punctuation where present. This catches `10` changing to `100` and `안` changing to `안녕하세요`; it is a lexical preservation guard, never semantic validation. Optional `speaker_labels` are explicit aligned `{reference, hypothesis}` pairs. Missing speaker labels remain `not_evaluated`, never a perfect score.

Thresholds are optional. Without them the verdict is `measured_not_gated`; no passing target is invented. With thresholds and complete review metadata, `gated_pass` or `gated_fail` incorporates CER/WER and the explicit lexical and speaker checks. The CLI exits 1 when any evaluated case is `gated_fail`, 2 for an invalid manifest or report-write failure, and 0 when it produced a report with no gated failures. Exit 0 for `measured_not_gated`, `not_reviewed`, or `not_evaluated` does not imply a quality pass. Reports contain hashes, metrics, and counts only—reference and hypothesis SHA-256 values support provenance without exposing transcript text, source paths, phrases, reviewer/evidence values, or speaker labels.

Exact edit distance is O(nm) in reference and hypothesis unit counts. Use bounded, representative gold clips instead of whole long meetings when evaluating CER, especially in routine local checks.
