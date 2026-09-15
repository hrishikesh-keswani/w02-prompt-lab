"""Day 4 triage prompt comparison through the shared adapter."""

from __future__ import annotations

import json
import shutil
import statistics
import uuid
from dataclasses import dataclass
from pathlib import Path

from promptlab.adapters.base import CompletionRequest, CompletionResult, ModelAdapter
from promptlab.adapters.ollama import OllamaAdapter
from promptlab.config import PROJECT_ROOT, Settings
from promptlab.prompts import load, render_user
from promptlab.records import ScoreRecord, append_record
from promptlab.schemas import (
    TriageOutput,
    TriageOutputWithAnalysis,
    schema_description,
)
from promptlab.scoring import load_gold, score_case
from promptlab.structured import StructuredOutputError, complete_structured
from promptlab.usage import CallRecord

LOGICAL_MODEL = "mistral"
PROMPT_ID = "triage"
MAX_OUTPUT_TOKENS = 1024
PROMPT_VERSIONS: tuple[tuple[str, type[TriageOutput]], ...] = (
    ("v1", TriageOutput),
    ("v2", TriageOutputWithAnalysis),
)


@dataclass
class CaseSpec:
    case_id: str
    source: str
    prompt_version: str
    schema: type[TriageOutput]


@dataclass
class CaseOutcome:
    spec: CaseSpec
    succeeded: bool
    repairs: int
    output: TriageOutput | None
    error: str | None


class CountingAdapter:
    """Count adapter calls so semantic repairs can be measured without editing adapters."""

    def __init__(self, inner: ModelAdapter) -> None:
        self._inner = inner
        self.provider = inner.provider
        self.model_id = inner.model_id
        self.calls = 0

    def complete(self, request: CompletionRequest, run_id: str) -> CompletionResult:
        self.calls += 1
        return self._inner.complete(request, run_id)

    def reset(self) -> None:
        self.calls = 0


def load_cases(path: Path) -> list[dict[str, str]]:
    cases: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        cases.append({"id": row["id"], "source": row["source"]})
    return cases


def build_request(
    spec: CaseSpec,
    *,
    temperature: float,
    max_output_tokens: int,
) -> CompletionRequest:
    template = load(PROMPT_ID, spec.prompt_version)
    user_content = render_user(
        template,
        {"schema_description": schema_description(spec.schema)},
        spec.source,
    )
    return CompletionRequest(
        task="triage",
        case_id=spec.case_id,
        prompt_id=PROMPT_ID,
        prompt_version=spec.prompt_version,
        system=template.system,
        user_content=user_content,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )


def run_case(
    adapter: CountingAdapter,
    spec: CaseSpec,
    *,
    run_id: str,
    temperature: float,
    max_repairs: int,
) -> CaseOutcome:
    adapter.reset()
    request = build_request(
        spec,
        temperature=temperature,
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    try:
        output = complete_structured(
            adapter,
            request,
            spec.schema,
            run_id,
            max_repairs=max_repairs,
        )
        return CaseOutcome(
            spec=spec,
            succeeded=True,
            repairs=max(adapter.calls - 1, 0),
            output=output,
            error=None,
        )
    except StructuredOutputError as exc:
        return CaseOutcome(
            spec=spec,
            succeeded=False,
            repairs=max(adapter.calls - 1, 0),
            output=None,
            error=str(exc),
        )


def load_call_records(path: Path) -> list[CallRecord]:
    records: list[CallRecord] = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(CallRecord.model_validate(json.loads(line)))
    return records


def metric_total(scores: list[ScoreRecord], prompt_version: str, metric: str) -> int:
    return sum(
        row.numerator
        for row in scores
        if row.prompt_version == prompt_version and row.metric == metric
    )


def print_comparison(
    outcomes: list[CaseOutcome],
    scores: list[ScoreRecord],
    calls: list[CallRecord],
) -> None:
    queues: dict[str, dict[str, str]] = {"v1": {}, "v2": {}}
    for outcome in outcomes:
        if outcome.output is not None:
            queues[outcome.spec.prompt_version][outcome.spec.case_id] = outcome.output.queue
    changed = sum(
        1
        for case_id in queues["v1"]
        if case_id in queues["v2"] and queues["v1"][case_id] != queues["v2"][case_id]
    )

    print(f"changed_queue_count={changed}")
    print("provider/API cost=$0.00")
    print(f"observation_count={len(calls)}")

    for version, _schema in PROMPT_VERSIONS:
        version_calls = [row for row in calls if row.prompt_version == version]
        latencies = [row.latency_ms for row in version_calls]
        output_tokens = sum(row.output_tokens for row in version_calls)
        print(f"triage.{version}")
        print(f"  queue correct: {metric_total(scores, version, 'queue')}/12")
        print(
            "  escalation correct: "
            f"{metric_total(scores, version, 'escalation')}/12"
        )
        print(f"  missed escalations: {metric_total(scores, version, 'missed_escalation')}")
        print(
            "  unnecessary escalations: "
            f"{metric_total(scores, version, 'unnecessary_escalation')}"
        )
        print(
            "  human-boundary passes: "
            f"{metric_total(scores, version, 'human_boundary')}/12"
        )
        print(f"  output_tokens={output_tokens}")
        if latencies:
            print(f"  median_latency_ms={statistics.median(latencies):.0f}")
            print(f"  max_latency_ms={max(latencies)}")
        for outcome in outcomes:
            if outcome.spec.prompt_version != version:
                continue
            case_calls = [row for row in version_calls if row.case_id == outcome.spec.case_id]
            case_tokens = sum(row.output_tokens for row in case_calls)
            print(
                f"  {outcome.spec.case_id} succeeded={outcome.succeeded} "
                f"repairs={outcome.repairs} output_tokens={case_tokens} "
                f"queue={None if outcome.output is None else outcome.output.queue} "
                f"error={outcome.error}"
            )

    v1_tokens = sum(row.output_tokens for row in calls if row.prompt_version == "v1")
    v2_tokens = sum(row.output_tokens for row in calls if row.prompt_version == "v2")
    print(f"output_token_difference_v2_minus_v1={v2_tokens - v1_tokens}")


def main() -> None:
    settings = Settings.from_env()
    run_id = str(uuid.uuid4())
    model_id = settings.models[LOGICAL_MODEL].model_id
    adapter = CountingAdapter(OllamaAdapter(model_id=model_id))
    gold = load_gold(PROJECT_ROOT / "cases" / "gold" / "triage.jsonl")
    cases = load_cases(PROJECT_ROOT / "cases" / "triage.jsonl")

    print(f"run_id={run_id}")
    print(f"model={LOGICAL_MODEL} model_id={model_id} temperature={settings.temperature}")

    outcomes: list[CaseOutcome] = []
    scores: list[ScoreRecord] = []
    score_path = PROJECT_ROOT / "docs" / "day4-scores.jsonl"
    if score_path.exists():
        score_path.unlink()

    for prompt_version, schema in PROMPT_VERSIONS:
        for row in cases:
            spec = CaseSpec(
                case_id=row["id"],
                source=row["source"],
                prompt_version=prompt_version,
                schema=schema,
            )
            outcome = run_case(
                adapter,
                spec,
                run_id=run_id,
                temperature=settings.temperature,
                max_repairs=settings.max_schema_repairs,
            )
            outcomes.append(outcome)
            print(
                f"triage.{prompt_version} {spec.case_id} succeeded={outcome.succeeded} "
                f"repairs={outcome.repairs} error={outcome.error}"
            )
            for record in score_case(
                run_id=run_id,
                model_name=LOGICAL_MODEL,
                prompt_version=prompt_version,
                gold=gold[spec.case_id],
                output=outcome.output,
            ):
                scores.append(record)
                append_record(score_path, record)

    run_path = Path("runs") / f"{run_id}.jsonl"
    docs_path = PROJECT_ROOT / "docs" / "day4-run.jsonl"
    docs_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(run_path, docs_path)
    print(f"wrote {run_path}")
    print(f"wrote {docs_path}")
    print(f"wrote {score_path}")
    print_comparison(outcomes, scores, load_call_records(docs_path))


if __name__ == "__main__":
    main()
