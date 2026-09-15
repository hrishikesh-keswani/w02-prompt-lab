"""Day 3 structured-output run through the shared adapter."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from promptlab.adapters.base import CompletionRequest, CompletionResult, ModelAdapter
from promptlab.adapters.ollama import OllamaAdapter
from promptlab.config import PROJECT_ROOT, Settings
from promptlab.schemas import (
    EvidenceField,
    PolicyExtraction,
    SummarizationOutput,
    TaskName,
    schema_description,
)
from promptlab.structured import StructuredOutputError, complete_structured

LOGICAL_MODEL = "mistral"
MAX_OUTPUT_TOKENS = 2048
SYSTEM_PROMPT = (
    "You are reviewing an internal operations document. "
    "Follow the user instructions exactly and use only the supplied document. "
    "Return only a JSON object."
)
LEAKAGE_MARKERS = (
    "Northglass",
    "Norwyn",
    "Bellwater",
    "Redhaven",
    "East Kestrel",
)
_NUMBERED_HEADING = re.compile(r"^\d+\.\s+\S.*$")
_MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+\S.*$")
_VALIDATION_ERROR_LABEL = "Validation error:"


@dataclass
class CaseSpec:
    task: TaskName
    case_id: str
    source: str
    prompt_id: str
    prompt_version: str
    prompt_template: str
    schema: type[BaseModel]


@dataclass
class CaseOutcome:
    spec: CaseSpec
    succeeded: bool
    repairs: int
    output: BaseModel | None
    texts: list[str]
    error: str | None
    repair_error: str | None = None


class CountingAdapter:
    """Count adapter calls so semantic repairs can be measured without editing adapters."""

    def __init__(self, inner: ModelAdapter) -> None:
        self._inner = inner
        self.provider = inner.provider
        self.model_id = inner.model_id
        self.calls = 0
        self.requests: list[CompletionRequest] = []
        self.texts: list[str] = []

    def complete(self, request: CompletionRequest, run_id: str) -> CompletionResult:
        self.calls += 1
        self.requests.append(request)
        result = self._inner.complete(request, run_id)
        if result.text:
            self.texts.append(result.text)
        return result

    def reset(self) -> None:
        self.calls = 0
        self.requests.clear()
        self.texts.clear()


def load_jsonl_cases(path: Path) -> list[dict[str, str]]:
    cases: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        cases.append({"id": row["id"], "source": row["source"]})
    return cases


def load_prompt(name: str) -> str:
    return (PROJECT_ROOT / "src" / "prompts" / name).read_text(encoding="utf-8")


def fill_prompt(template: str, *, document_text: str, schema: type[BaseModel]) -> str:
    return template.replace("{schema_description}", schema_description(schema)).replace(
        "{document_text}", document_text
    )


def build_request(
    spec: CaseSpec,
    *,
    temperature: float,
    max_output_tokens: int,
) -> CompletionRequest:
    return CompletionRequest(
        task=spec.task,
        case_id=spec.case_id,
        prompt_id=spec.prompt_id,
        prompt_version=spec.prompt_version,
        system=SYSTEM_PROMPT,
        user_content=fill_prompt(
            spec.prompt_template,
            document_text=spec.source,
            schema=spec.schema,
        ),
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )


def source_headings(source: str) -> list[str]:
    headings: list[str] = []
    for raw in source.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _NUMBERED_HEADING.match(line):
            headings.append(line)
            _prefix, _sep, rest = line.partition(". ")
            if rest:
                headings.append(rest.strip())
        elif _MARKDOWN_HEADING.match(line):
            headings.append(line)
            headings.append(re.sub(r"^#{1,6}\s+", "", line).strip())
    return headings


def citation_matches_heading(citation: str, headings: list[str]) -> bool:
    cite = citation.strip()
    if not cite:
        return False
    return any(cite == heading or cite in heading for heading in headings)


def evidence_fields_from(output: BaseModel) -> dict[str, EvidenceField]:
    if isinstance(output, SummarizationOutput | PolicyExtraction):
        return output.evidence_fields()
    return {}


def citation_existence_failures(outcome: CaseOutcome) -> int:
    if outcome.output is None:
        return 0
    headings = source_headings(outcome.spec.source)
    failures = 0
    for field in evidence_fields_from(outcome.output).values():
        if field.status != "present":
            continue
        if field.citation is None or not citation_matches_heading(field.citation, headings):
            failures += 1
    return failures


def leakage_markers_found(outcome: CaseOutcome) -> list[str]:
    blob = "\n".join(outcome.texts)
    if outcome.output is not None:
        blob = f"{blob}\n{outcome.output.model_dump_json()}"
    return [marker for marker in LEAKAGE_MARKERS if marker in blob]


def repair_error_from(adapter: CountingAdapter) -> str | None:
    if len(adapter.requests) < 2:
        return None
    text = adapter.requests[1].user_content
    if _VALIDATION_ERROR_LABEL not in text:
        return "schema validation failed"
    return text.split(_VALIDATION_ERROR_LABEL, 1)[1].strip()


def run_case(
    adapter: CountingAdapter,
    spec: CaseSpec,
    *,
    run_id: str,
    temperature: float,
    max_repairs: int,
) -> CaseOutcome:
    adapter.reset()
    request = build_request(spec, temperature=temperature, max_output_tokens=MAX_OUTPUT_TOKENS)
    try:
        output = complete_structured(
            adapter,
            request,
            spec.schema,
            run_id,
            max_repairs=max_repairs,
        )
        repairs = max(adapter.calls - 1, 0)
        return CaseOutcome(
            spec=spec,
            succeeded=True,
            repairs=repairs,
            output=output,
            texts=list(adapter.texts),
            error=None,
            repair_error=repair_error_from(adapter) if repairs else None,
        )
    except StructuredOutputError as exc:
        repairs = max(adapter.calls - 1, 0)
        return CaseOutcome(
            spec=spec,
            succeeded=False,
            repairs=repairs,
            output=None,
            texts=list(adapter.texts),
            error=str(exc),
            repair_error=repair_error_from(adapter) or str(exc),
        )


def load_specs() -> list[CaseSpec]:
    summarize_prompt = load_prompt("summarize.v1.md")
    extract_prompt = load_prompt("extract.v2.md")
    specs: list[CaseSpec] = []
    for row in load_jsonl_cases(PROJECT_ROOT / "cases" / "summarization.jsonl"):
        specs.append(
            CaseSpec(
                task="summarization",
                case_id=row["id"],
                source=row["source"],
                prompt_id="summarize",
                prompt_version="v1",
                prompt_template=summarize_prompt,
                schema=SummarizationOutput,
            )
        )
    for row in load_jsonl_cases(PROJECT_ROOT / "cases" / "extraction.jsonl"):
        specs.append(
            CaseSpec(
                task="extraction",
                case_id=row["id"],
                source=row["source"],
                prompt_id="extract",
                prompt_version="v2",
                prompt_template=extract_prompt,
                schema=PolicyExtraction,
            )
        )
    return specs


def print_metrics(outcomes: list[CaseOutcome]) -> None:
    by_task: dict[TaskName, list[CaseOutcome]] = {"summarization": [], "extraction": []}
    for outcome in outcomes:
        if outcome.spec.task in by_task:
            by_task[outcome.spec.task].append(outcome)

    for task, rows in by_task.items():
        repaired = sum(1 for row in rows if row.repairs > 0)
        succeeded = sum(1 for row in rows if row.succeeded)
        print(f"{task}_repair_rate={repaired}/{len(rows)}")
        print(f"{task}_schema_success={succeeded}/{len(rows)}")

    extraction = by_task["extraction"]
    leakage_hits = [marker for row in extraction for marker in leakage_markers_found(row)]
    citation_failures = sum(citation_existence_failures(row) for row in outcomes)
    print(f"example_leakage_count={len(leakage_hits)}")
    print(f"citation_existence_failure_count={citation_failures}")
    if leakage_hits:
        details = [
            f"{row.spec.case_id}:{marker}"
            for row in extraction
            for marker in leakage_markers_found(row)
        ]
        print("leakage_hits=" + ",".join(details))


def main() -> None:
    settings = Settings.from_env()
    run_id = str(uuid.uuid4())
    model_id = settings.models[LOGICAL_MODEL].model_id
    adapter = CountingAdapter(OllamaAdapter(model_id=model_id))
    specs = load_specs()

    print(f"run_id={run_id}")
    print(f"model={LOGICAL_MODEL} model_id={model_id} temperature={settings.temperature}")

    outcomes: list[CaseOutcome] = []
    for spec in specs:
        outcome = run_case(
            adapter,
            spec,
            run_id=run_id,
            temperature=settings.temperature,
            max_repairs=settings.max_schema_repairs,
        )
        outcomes.append(outcome)
        print(
            f"{spec.task} {spec.case_id} succeeded={outcome.succeeded} "
            f"repairs={outcome.repairs} error={outcome.error}"
        )

    run_path = Path("runs") / f"{run_id}.jsonl"
    docs_path = PROJECT_ROOT / "docs" / "day3-run.jsonl"
    docs_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(run_path, docs_path)
    print(f"wrote {run_path}")
    print(f"wrote {docs_path}")
    print_metrics(outcomes)


if __name__ == "__main__":
    main()
