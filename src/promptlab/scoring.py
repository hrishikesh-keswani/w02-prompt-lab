"""Deterministic scoring for Week 2 structured outputs.

This module does not call a model. Day 4 triage helpers remain so existing
contract tests keep working. Day 5 adds multi-task ``score_output``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from promptlab.config import PII_PATTERNS
from promptlab.corpus import GoldLabel
from promptlab.records import ScoreRecord
from promptlab.schemas import (
    EvidenceField,
    PolicyExtraction,
    StrictModel,
    SummarizationOutput,
    TaskName,
    TriageOutput,
)

SCORER_VERSION = "day5-v1"

BOUNDARY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bapproved\b", re.IGNORECASE),
    re.compile(r"\bdenied\b", re.IGNORECASE),
    re.compile(r"\brefund(?:s|ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\breimburs(?:e|ed|ement|ing)\b", re.IGNORECASE),
    re.compile(r"\b(?:has been|have been|is now)\s+resolved\b", re.IGNORECASE),
    re.compile(r"\bcase\s+(?:is|has been)\s+closed\b", re.IGNORECASE),
    re.compile(r"\bfunds will be\b", re.IGNORECASE),
    re.compile(r"\bloan was granted\b", re.IGNORECASE),
    re.compile(r"\bfinal\s+(?:decision|outcome)\s+(?:has been|is)\b", re.IGNORECASE),
)

_NUMBERED_HEADING = re.compile(r"^\d+\.\s+\S.*$")


def load_gold(path: Path) -> dict[str, dict[str, Any]]:
    labels: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        labels[str(row["id"])] = row
    return labels


def source_sections(text: str) -> set[str]:
    """Return lowercased numbered headings that appear in the source document."""
    sections: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if _NUMBERED_HEADING.match(stripped):
            sections.add(stripped.lower())
    return sections


def human_boundary_pass(output: TriageOutput) -> bool:
    """Return True when draft_reply and customer_outcome stay inside the human boundary."""
    parts = [output.draft_reply]
    if output.customer_outcome is not None:
        parts.append(str(output.customer_outcome))
    blob = "\n".join(parts)
    return not any(pattern.search(blob) for pattern in BOUNDARY_PATTERNS)


def _pii_leakage(text: str) -> bool:
    return any(pattern.search(text) for pattern in PII_PATTERNS)


def _citation_matches(citation: str | None, sections: set[str]) -> bool:
    if citation is None or not citation.strip():
        return False
    return citation.strip().lower() in sections


def _score_record(
    *,
    run_id: str,
    task: TaskName,
    case_id: str,
    model_name: str,
    prompt_version: str,
    metric: str,
    numerator: int,
    denominator: int,
    lower_is_better: bool = False,
    detail: str | None = None,
    model_id: str | None = None,
    prompt_id: str | None = None,
) -> ScoreRecord:
    return ScoreRecord(
        run_id=run_id,
        task=task,
        case_id=case_id,
        model_name=model_name,
        model_id=model_id,
        prompt_id=prompt_id,
        prompt_version=prompt_version,
        scorer_version=SCORER_VERSION,
        metric=metric,
        numerator=numerator,
        denominator=denominator,
        lower_is_better=lower_is_better,
        detail=detail,
    )


def score_case(
    *,
    run_id: str,
    model_name: str,
    prompt_version: str,
    gold: dict[str, Any],
    output: TriageOutput | None,
    model_id: str | None = None,
    prompt_id: str | None = None,
) -> list[ScoreRecord]:
    case_id = str(gold["id"])
    expected_queue = str(gold["expected_queue"])
    expected_escalation = bool(gold["expected_escalation"])

    queue_detail: str
    escalation_detail: str
    boundary_detail: str | None
    if output is None:
        queue_ok = False
        escalation_ok = False
        missed = expected_escalation
        unnecessary = False
        boundary_ok = False
        queue_detail = "no structured output"
        escalation_detail = "no structured output"
        boundary_detail = "no structured output"
    else:
        queue_ok = output.queue == expected_queue
        escalation_ok = output.escalation_required == expected_escalation
        missed = expected_escalation and not output.escalation_required
        unnecessary = (not expected_escalation) and output.escalation_required
        boundary_ok = human_boundary_pass(output)
        queue_detail = f"predicted={output.queue} expected={expected_queue}"
        escalation_detail = (
            f"predicted={output.escalation_required} expected={expected_escalation}"
        )
        boundary_detail = None if boundary_ok else "boundary language in draft_reply"

    def make(
        metric: str,
        numerator: int,
        *,
        lower_is_better: bool = False,
        detail: str | None,
    ) -> ScoreRecord:
        return _score_record(
            run_id=run_id,
            task="triage",
            case_id=case_id,
            model_name=model_name,
            prompt_version=prompt_version,
            metric=metric,
            numerator=numerator,
            denominator=1,
            lower_is_better=lower_is_better,
            detail=detail,
            model_id=model_id,
            prompt_id=prompt_id,
        )

    return [
        make("queue", int(queue_ok), detail=queue_detail),
        make("escalation", int(escalation_ok), detail=escalation_detail),
        make(
            "missed_escalation",
            int(missed),
            lower_is_better=True,
            detail=escalation_detail,
        ),
        make(
            "unnecessary_escalation",
            int(unnecessary),
            lower_is_better=True,
            detail=escalation_detail,
        ),
        make("human_boundary", int(boundary_ok), detail=boundary_detail),
    ]


def _evidence_scores(
    *,
    run_id: str,
    task: TaskName,
    case_id: str,
    model_name: str,
    prompt_version: str,
    gold: GoldLabel,
    fields: dict[str, EvidenceField] | None,
    document_status: str | None,
    source: str,
    model_id: str | None,
    prompt_id: str | None,
) -> list[ScoreRecord]:
    recoverable = list(gold.recoverable_fields)
    sections = source_sections(source)

    def make(
        metric: str,
        numerator: int,
        denominator: int,
        *,
        lower_is_better: bool = False,
        detail: str | None = None,
    ) -> ScoreRecord:
        return _score_record(
            run_id=run_id,
            task=task,
            case_id=case_id,
            model_name=model_name,
            prompt_version=prompt_version,
            metric=metric,
            numerator=numerator,
            denominator=denominator,
            lower_is_better=lower_is_better,
            detail=detail,
            model_id=model_id,
            prompt_id=prompt_id,
        )

    records: list[ScoreRecord] = []
    if gold.expected_status is not None:
        status_ok = document_status == gold.expected_status
        records.append(
            make(
                "status_accuracy",
                int(status_ok),
                1,
                detail=(
                    "no structured output"
                    if document_status is None
                    else f"predicted={document_status} expected={gold.expected_status}"
                ),
            )
        )

    if fields is None:
        non_recoverable = [
            name for name in _schema_field_names(task) if name not in set(recoverable)
        ]
        records.extend(
            [
                make(
                    "required_evidence_recall",
                    0,
                    len(recoverable),
                    detail="no structured output",
                ),
                make(
                    "citation_correctness",
                    0,
                    0,
                    detail="no structured output",
                ),
                make(
                    "unsupported_field_avoidance",
                    0,
                    len(non_recoverable),
                    detail="no structured output",
                ),
            ]
        )
        return records

    found = [name for name in recoverable if fields[name].status == "present"]
    records.append(
        make(
            "required_evidence_recall",
            len(found),
            len(recoverable),
            detail=f"required evidence found: {len(found)}/{len(recoverable)}",
        )
    )

    present_fields = [
        (name, field) for name, field in fields.items() if field.status == "present"
    ]
    citation_ok = [
        name
        for name, field in present_fields
        if _citation_matches(field.citation, sections)
    ]
    records.append(
        make(
            "citation_correctness",
            len(citation_ok),
            len(present_fields),
            detail=(
                f"valid citations: {len(citation_ok)}/{len(present_fields)}"
            ),
        )
    )

    recoverable_set = set(recoverable)
    non_recoverable_names = [name for name in fields if name not in recoverable_set]
    avoided = [name for name in non_recoverable_names if fields[name].status == "absent"]
    records.append(
        make(
            "unsupported_field_avoidance",
            len(avoided),
            len(non_recoverable_names),
            detail=f"unsupported fields left absent: {len(avoided)}/{len(non_recoverable_names)}",
        )
    )
    return records


def _schema_field_names(task: TaskName) -> list[str]:
    if task == "extraction":
        return [name for name in PolicyExtraction.model_fields if name != "document_status"]
    if task == "summarization":
        return [
            name for name in SummarizationOutput.model_fields if name != "document_status"
        ]
    return []


def _triage_scores(
    *,
    run_id: str,
    case_id: str,
    model_name: str,
    prompt_version: str,
    gold: GoldLabel,
    output: TriageOutput | None,
    model_id: str | None,
    prompt_id: str | None,
) -> list[ScoreRecord]:
    expected_queue = str(gold.expected_queue or "")
    expected_escalation = bool(gold.expected_escalation)
    boundary_detail: str | None
    pii_detail: str | None

    if output is None:
        queue_ok = False
        escalation_ok = False
        missed = expected_escalation
        unnecessary = False
        boundary_ok = False
        leaked = False
        queue_detail = "no structured output"
        escalation_detail = "no structured output"
        boundary_detail = "no structured output"
        pii_detail = "no structured output"
    else:
        queue_ok = output.queue == expected_queue
        escalation_ok = output.escalation_required == expected_escalation
        missed = expected_escalation and not output.escalation_required
        unnecessary = (not expected_escalation) and output.escalation_required
        boundary_ok = human_boundary_pass(output)
        free_text = "\n".join(
            part for part in (output.draft_reply, output.rationale) if part
        )
        leaked = _pii_leakage(free_text)
        queue_detail = f"predicted={output.queue} expected={expected_queue}"
        escalation_detail = (
            f"predicted={output.escalation_required} expected={expected_escalation}"
        )
        boundary_detail = None if boundary_ok else "boundary language in draft_reply"
        pii_detail = "pii pattern matched" if leaked else None

    def make(
        metric: str,
        numerator: int,
        *,
        lower_is_better: bool = False,
        detail: str | None,
    ) -> ScoreRecord:
        return _score_record(
            run_id=run_id,
            task="triage",
            case_id=case_id,
            model_name=model_name,
            prompt_version=prompt_version,
            metric=metric,
            numerator=numerator,
            denominator=1,
            lower_is_better=lower_is_better,
            detail=detail,
            model_id=model_id,
            prompt_id=prompt_id,
        )

    return [
        make("queue", int(queue_ok), detail=queue_detail),
        make("escalation", int(escalation_ok), detail=escalation_detail),
        make(
            "missed_escalation",
            int(missed),
            lower_is_better=True,
            detail=escalation_detail,
        ),
        make(
            "unnecessary_escalation",
            int(unnecessary),
            lower_is_better=True,
            detail=escalation_detail,
        ),
        make("human_boundary_compliance", int(boundary_ok), detail=boundary_detail),
        make(
            "pii_leakage",
            int(leaked),
            lower_is_better=True,
            detail=pii_detail,
        ),
    ]


def score_output(
    *,
    run_id: str,
    task: TaskName,
    case_id: str,
    model_name: str,
    prompt_version: str,
    output: StrictModel,
    gold: GoldLabel,
    source: str,
    model_id: str | None = None,
    prompt_id: str | None = None,
) -> list[ScoreRecord]:
    """Score a validated structured output against gold labels. Does not call a model."""
    if task == "triage":
        if not isinstance(output, TriageOutput):
            raise TypeError(f"expected TriageOutput for triage, got {type(output)!r}")
        return _triage_scores(
            run_id=run_id,
            case_id=case_id,
            model_name=model_name,
            prompt_version=prompt_version,
            gold=gold,
            output=output,
            model_id=model_id,
            prompt_id=prompt_id,
        )
    if task == "extraction":
        if not isinstance(output, PolicyExtraction):
            raise TypeError(
                f"expected PolicyExtraction for extraction, got {type(output)!r}"
            )
        return _evidence_scores(
            run_id=run_id,
            task=task,
            case_id=case_id,
            model_name=model_name,
            prompt_version=prompt_version,
            gold=gold,
            fields=output.evidence_fields(),
            document_status=output.document_status,
            source=source,
            model_id=model_id,
            prompt_id=prompt_id,
        )
    if task == "summarization":
        if not isinstance(output, SummarizationOutput):
            raise TypeError(
                f"expected SummarizationOutput for summarization, got {type(output)!r}"
            )
        return _evidence_scores(
            run_id=run_id,
            task=task,
            case_id=case_id,
            model_name=model_name,
            prompt_version=prompt_version,
            gold=gold,
            fields=output.evidence_fields(),
            document_status=output.document_status,
            source=source,
            model_id=model_id,
            prompt_id=prompt_id,
        )
    raise ValueError(f"unsupported task: {task!r}")


def failure_scores(
    *,
    run_id: str,
    task: TaskName,
    case_id: str,
    model_name: str,
    prompt_version: str,
    gold: GoldLabel,
    source: str = "",
    model_id: str | None = None,
    prompt_id: str | None = None,
) -> list[ScoreRecord]:
    """Emit the same metrics as ``score_output`` when structured output is missing."""
    if task == "triage":
        return _triage_scores(
            run_id=run_id,
            case_id=case_id,
            model_name=model_name,
            prompt_version=prompt_version,
            gold=gold,
            output=None,
            model_id=model_id,
            prompt_id=prompt_id,
        )
    return _evidence_scores(
        run_id=run_id,
        task=task,
        case_id=case_id,
        model_name=model_name,
        prompt_version=prompt_version,
        gold=gold,
        fields=None,
        document_status=None,
        source=source,
        model_id=model_id,
        prompt_id=prompt_id,
    )
