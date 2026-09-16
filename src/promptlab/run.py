"""Day 5 evaluation harness: three tasks, two local Ollama models, gold scoring."""

from __future__ import annotations

import argparse
import re
import shutil
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

from promptlab.adapters.base import CompletionRequest, CompletionResult, ModelAdapter
from promptlab.adapters.ollama import OllamaAdapter
from promptlab.config import PROJECT_ROOT, ModelConfig, Settings
from promptlab.corpus import GoldLabel, load_cases, validate_corpus
from promptlab.prompts import load, render_user
from promptlab.records import OutputRecord, ScoreRecord, UsageRecord, append_record
from promptlab.report import write_reports
from promptlab.rules import VersionCandidate, select_current_version
from promptlab.schemas import (
    OUTPUT_SCHEMAS,
    PolicyExtraction,
    StrictModel,
    SummarizationOutput,
    TaskName,
    schema_description,
)
from promptlab.scoring import SCORER_VERSION, failure_scores, score_output
from promptlab.structured import StructuredOutputError, complete_structured
from promptlab.usage import CallRecord

RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")

TASK_PROMPTS: dict[TaskName, tuple[str, str]] = {
    "summarization": ("summarize", "v1"),
    "extraction": ("extract", "v2"),
    "triage": ("triage", "v1"),
}

MAX_OUTPUT_TOKENS: dict[TaskName, int] = {
    "triage": 1024,
    "summarization": 2048,
    "extraction": 2048,
}

DEFAULT_SYSTEM = (
    "You are reviewing an internal operations document. "
    "Follow the user instructions exactly and use only the supplied document. "
    "Return only a JSON object."
)

UsageKind = Literal["primary", "transport_retry", "repair", "repair_retry"]
UsageStatus = Literal["success", "schema_invalid", "transport_error"]


class RecordingAdapter:
    """Count adapter calls and retain CallRecords without editing the Ollama adapter."""

    def __init__(self, inner: ModelAdapter) -> None:
        self._inner = inner
        self.provider = inner.provider
        self.model_id = inner.model_id
        self.calls = 0
        self.batches: list[list[CallRecord]] = []

    def complete(self, request: CompletionRequest, run_id: str) -> CompletionResult:
        self.calls += 1
        result = self._inner.complete(request, run_id)
        self.batches.append(list(result.records))
        return result

    def reset(self) -> None:
        self.calls = 0
        self.batches.clear()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local two-model prompt comparison")
    parser.add_argument("--run-id", help="Stable identifier for this run")
    parser.add_argument("--task", choices=["triage", "summarization", "extraction"])
    parser.add_argument("--model", choices=["mistral", "qwen"])
    parser.add_argument("--limit", type=int, help="Limit cases per task for a smoke run")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate configuration and corpus without calling Ollama",
    )
    return parser


def _kind_for_attempt(batch_index: int, attempt: int) -> UsageKind:
    if batch_index == 0:
        return "primary" if attempt == 1 else "transport_retry"
    return "repair" if attempt == 1 else "repair_retry"


def _status_for_record(
    record: CallRecord,
    *,
    batch_index: int,
    batch_count: int,
    succeeded: bool,
) -> UsageStatus:
    if record.error_type is not None:
        return "transport_error"
    is_last_batch = batch_index == batch_count - 1
    if succeeded and is_last_batch:
        return "success"
    return "schema_invalid"


def _usage_records(
    *,
    run_id: str,
    task: TaskName,
    case_id: str,
    model: ModelConfig,
    prompt_id: str,
    prompt_version: str,
    batches: list[list[CallRecord]],
    succeeded: bool,
) -> list[UsageRecord]:
    records: list[UsageRecord] = []
    batch_count = len(batches)
    for batch_index, batch in enumerate(batches):
        for call in batch:
            records.append(
                UsageRecord(
                    run_id=run_id,
                    task=task,
                    case_id=case_id,
                    model_name=model.logical_name,
                    model_id=model.model_id,
                    prompt_id=prompt_id,
                    prompt_version=prompt_version,
                    attempt=call.attempt,
                    kind=_kind_for_attempt(batch_index, call.attempt),
                    status=_status_for_record(
                        call,
                        batch_index=batch_index,
                        batch_count=batch_count,
                        succeeded=succeeded,
                    ),
                    prompt_tokens=call.input_tokens,
                    completion_tokens=call.output_tokens,
                    latency_ms=float(call.latency_ms),
                    cost_usd=Decimal(str(call.cost_usd)),
                    error=call.error_type,
                )
            )
    return records


def _version_fields(output: StrictModel) -> tuple[str, str] | None:
    if not isinstance(output, SummarizationOutput | PolicyExtraction):
        return None
    version = output.version
    effective = output.effective_date
    if (
        version.status == "present"
        and effective.status == "present"
        and isinstance(version.value, str)
        and isinstance(effective.value, str)
    ):
        return version.value, effective.value
    return None


def _add_version_scores(
    *,
    run_id: str,
    task: TaskName,
    model: ModelConfig,
    prompt_id: str,
    prompt_version: str,
    labels: list[GoldLabel],
    outputs: dict[str, StrictModel],
    scores_path: Path,
    all_scores: list[ScoreRecord],
) -> None:
    grouped: dict[str, list[GoldLabel]] = defaultdict(list)
    for label in labels:
        if label.version_group:
            grouped[label.version_group].append(label)

    for group_name, group_labels in grouped.items():
        if len(group_labels) < 2:
            continue
        expected = next(
            (
                label.expected_current_case_id
                for label in group_labels
                if label.expected_current_case_id
            ),
            None,
        )
        as_of_raw = next((label.as_of for label in group_labels if label.as_of), None)
        if expected is None or as_of_raw is None:
            continue
        candidates: list[VersionCandidate] = []
        for label in group_labels:
            output = outputs.get(label.id)
            if output is None:
                continue
            extracted = _version_fields(output)
            if extracted is None:
                continue
            version, effective_raw = extracted
            try:
                effective = date.fromisoformat(effective_raw)
            except ValueError:
                continue
            candidates.append(
                VersionCandidate(case_id=label.id, version=version, effective_date=effective)
            )
        selected = select_current_version(candidates, date.fromisoformat(as_of_raw))
        record = ScoreRecord(
            run_id=run_id,
            task=task,
            case_id=f"version:{group_name}",
            model_name=model.logical_name,
            model_id=model.model_id,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            scorer_version=SCORER_VERSION,
            metric="version_selection_accuracy",
            numerator=int(selected is not None and selected.case_id == expected),
            denominator=1,
            detail=f"expected={expected}; selected={selected.case_id if selected else 'none'}",
        )
        append_record(scores_path, record)
        all_scores.append(record)


def _build_request(
    *,
    task: TaskName,
    case_id: str,
    document_text: str,
    temperature: float,
) -> CompletionRequest:
    prompt_id, prompt_version = TASK_PROMPTS[task]
    schema = OUTPUT_SCHEMAS[task]
    template = load(prompt_id, prompt_version)
    user_content = render_user(
        template,
        {"schema_description": schema_description(schema)},
        document_text,
    )
    system = template.system if template.system else DEFAULT_SYSTEM
    return CompletionRequest(
        task=task,
        case_id=case_id,
        prompt_id=prompt_id,
        prompt_version=prompt_version,
        system=system,
        user_content=user_content,
        temperature=temperature,
        max_output_tokens=MAX_OUTPUT_TOKENS[task],
    )


def main() -> None:
    args = _parser().parse_args()
    counts = validate_corpus()
    if args.validate_only:
        print("Corpus valid: " + ", ".join(f"{task}={count}" for task, count in counts.items()))
        return

    run_id = cast(str | None, args.run_id)
    if run_id is None or not RUN_ID_PATTERN.fullmatch(run_id):
        raise SystemExit("--run-id is required and must use letters, numbers, '.', '_' or '-'")
    limit = cast(int | None, args.limit)
    if limit is not None and limit < 1:
        raise SystemExit("--limit must be at least 1")

    selected_tasks: list[TaskName]
    if args.task:
        selected_tasks = [cast(TaskName, args.task)]
    else:
        selected_tasks = ["triage", "summarization", "extraction"]

    settings = Settings.from_env()
    selected_models = [cast(str, args.model)] if args.model else list(settings.models)
    run_dir = PROJECT_ROOT / "runs" / run_id
    if run_dir.exists():
        raise SystemExit(f"Run directory already exists: {run_dir}")
    run_dir.mkdir(parents=True)
    usage_path = run_dir / "usage.jsonl"
    outputs_path = run_dir / "outputs.jsonl"
    scores_path = run_dir / "scores.jsonl"

    adapters: dict[str, RecordingAdapter] = {}
    for model_name in selected_models:
        model = settings.models[model_name]
        adapters[model_name] = RecordingAdapter(
            OllamaAdapter(model_id=model.model_id, think=False)
        )

    all_usage: list[UsageRecord] = []
    all_outputs: list[OutputRecord] = []
    all_scores: list[ScoreRecord] = []
    total_cost = Decimal("0")
    validated_by_task_model: dict[tuple[TaskName, str], dict[str, StrictModel]] = defaultdict(
        dict
    )
    labels_by_task: dict[TaskName, list[GoldLabel]] = defaultdict(list)

    for task in selected_tasks:
        pairs = load_cases(task)
        if limit is not None:
            pairs = pairs[:limit]
        labels_by_task[task] = [gold for _case, gold in pairs]
        prompt_id, prompt_version = TASK_PROMPTS[task]
        schema = OUTPUT_SCHEMAS[task]
        for model_name in selected_models:
            model = settings.models[model_name]
            adapter = adapters[model_name]
            for case, gold in pairs:
                if total_cost >= settings.per_run_cap_usd and settings.per_run_cap_usd > 0:
                    raise SystemExit(
                        f"Per-run cost cap reached before {task}/{model_name}/{case.id}"
                    )
                adapter.reset()
                request = _build_request(
                    task=task,
                    case_id=case.id,
                    document_text=case.document_text,
                    temperature=settings.temperature,
                )
                try:
                    output = complete_structured(
                        adapter,
                        request,
                        schema,
                        run_id,
                        max_repairs=settings.max_schema_repairs,
                    )
                    succeeded = True
                    error: str | None = None
                    validated_by_task_model[(task, model_name)][case.id] = output
                    case_scores = score_output(
                        run_id=run_id,
                        task=task,
                        case_id=case.id,
                        model_name=model_name,
                        prompt_version=prompt_version,
                        output=output,
                        gold=gold,
                        source=case.document_text,
                        model_id=model.model_id,
                        prompt_id=prompt_id,
                    )
                except StructuredOutputError as exc:
                    succeeded = False
                    error = str(exc)
                    output = None
                    case_scores = failure_scores(
                        run_id=run_id,
                        task=task,
                        case_id=case.id,
                        model_name=model_name,
                        prompt_version=prompt_version,
                        gold=gold,
                        source=case.document_text,
                        model_id=model.model_id,
                        prompt_id=prompt_id,
                    )

                repairs = max(adapter.calls - 1, 0)
                for usage_record in _usage_records(
                    run_id=run_id,
                    task=task,
                    case_id=case.id,
                    model=model,
                    prompt_id=prompt_id,
                    prompt_version=prompt_version,
                    batches=adapter.batches,
                    succeeded=succeeded,
                ):
                    append_record(usage_path, usage_record)
                    all_usage.append(usage_record)
                    total_cost += usage_record.cost_usd

                output_record = OutputRecord(
                    run_id=run_id,
                    task=task,
                    case_id=case.id,
                    model_name=model_name,
                    model_id=model.model_id,
                    prompt_id=prompt_id,
                    prompt_version=prompt_version,
                    succeeded=succeeded,
                    repairs=repairs,
                    output=None if output is None else output.model_dump(mode="json"),
                    error=error,
                )
                append_record(outputs_path, output_record)
                all_outputs.append(output_record)
                for score in case_scores:
                    append_record(scores_path, score)
                    all_scores.append(score)
                print(
                    f"{task:13} {model_name:8} {case.id:5} "
                    f"{'ok' if succeeded else 'failed'} repairs={repairs}"
                )

    for task in selected_tasks:
        if task == "triage":
            continue
        prompt_id, prompt_version = TASK_PROMPTS[task]
        for model_name in selected_models:
            _add_version_scores(
                run_id=run_id,
                task=task,
                model=settings.models[model_name],
                prompt_id=prompt_id,
                prompt_version=prompt_version,
                labels=labels_by_task[task],
                outputs=validated_by_task_model[(task, model_name)],
                scores_path=scores_path,
                all_scores=all_scores,
            )

    call_log = Path("runs") / f"{run_id}.jsonl"
    docs_run = PROJECT_ROOT / "docs" / "day5-run.jsonl"
    docs_scores = PROJECT_ROOT / "docs" / "day5-scores.jsonl"
    docs_run.parent.mkdir(parents=True, exist_ok=True)
    if call_log.exists():
        shutil.copyfile(call_log, docs_run)
    docs_scores.write_text(
        "".join(record.model_dump_json() + "\n" for record in all_scores),
        encoding="utf-8",
    )

    write_reports(
        run_id=run_id,
        models=selected_models,
        usage=all_usage,
        outputs=all_outputs,
        scores=all_scores,
        report_path=PROJECT_ROOT / "reports" / "comparison.md",
        decision_path=PROJECT_ROOT / "docs" / "model-decision.md",
    )
    print(f"Report: {PROJECT_ROOT / 'reports' / 'comparison.md'}")
    print(f"Call records: {docs_run}")
    print(f"Score records: {docs_scores}")
    print(f"Recorded provider cost: ${total_cost}")


if __name__ == "__main__":
    main()
