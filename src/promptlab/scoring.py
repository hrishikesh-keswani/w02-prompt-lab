"""Deterministic Day 4 triage scoring. This module does not call a model."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from promptlab.records import ScoreRecord
from promptlab.schemas import TriageOutput

SCORER_VERSION = "day4-v1"

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


def load_gold(path: Path) -> dict[str, dict[str, Any]]:
    labels: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        labels[str(row["id"])] = row
    return labels


def human_boundary_pass(output: TriageOutput) -> bool:
    """Return True when draft_reply and customer_outcome stay inside the human boundary."""
    parts = [output.draft_reply]
    if output.customer_outcome is not None:
        parts.append(str(output.customer_outcome))
    blob = "\n".join(parts)
    return not any(pattern.search(blob) for pattern in BOUNDARY_PATTERNS)


def score_case(
    *,
    run_id: str,
    model_name: str,
    prompt_version: str,
    gold: dict[str, Any],
    output: TriageOutput | None,
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
        return ScoreRecord(
            run_id=run_id,
            task="triage",
            case_id=case_id,
            model_name=model_name,
            prompt_version=prompt_version,
            scorer_version=SCORER_VERSION,
            metric=metric,
            numerator=numerator,
            denominator=1,
            lower_is_better=lower_is_better,
            detail=detail,
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
