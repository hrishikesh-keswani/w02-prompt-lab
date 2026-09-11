"""Day 2 two-model summarization run through the shared adapter."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from promptlab.adapters.base import CompletionRequest
from promptlab.adapters.ollama import OllamaAdapter
from promptlab.config import PROJECT_ROOT, Settings

PROMPT_ID = "baseline"
PROMPT_VERSION = "v0"
MAX_OUTPUT_TOKENS = 256
LOGICAL_MODELS = ("mistral", "qwen")
SYSTEM_PROMPT = (
    "You are reviewing an internal operations document. "
    "Follow the user instructions exactly and use only the supplied document."
)


def load_cases() -> list[dict[str, str]]:
    path = PROJECT_ROOT / "cases" / "summarization.jsonl"
    cases: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        cases.append({"id": row["id"], "source": row["source"]})
    return cases


def load_prompt() -> str:
    return (PROJECT_ROOT / "src" / "prompts" / "baseline.v0.md").read_text(encoding="utf-8")


def build_request(
    *,
    case: dict[str, str],
    prompt_template: str,
    temperature: float,
) -> CompletionRequest:
    return CompletionRequest(
        task="summarization",
        case_id=case["id"],
        prompt_id=PROMPT_ID,
        prompt_version=PROMPT_VERSION,
        system=SYSTEM_PROMPT,
        user_content=prompt_template.replace("{document_text}", case["source"]),
        temperature=temperature,
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )


def main() -> None:
    settings = Settings.from_env()
    run_id = str(uuid.uuid4())
    cases = load_cases()
    prompt_template = load_prompt()

    print(f"run_id={run_id}")

    for logical_name in LOGICAL_MODELS:
        model_id = settings.models[logical_name].model_id
        adapter = OllamaAdapter(model_id=model_id)
        print(f"model={logical_name} model_id={model_id}")
        for case in cases:
            request = build_request(
                case=case,
                prompt_template=prompt_template,
                temperature=settings.temperature,
            )
            result = adapter.complete(request, run_id)
            last = result.records[-1]
            print(
                f"{logical_name} {case['id']} succeeded={result.succeeded} "
                f"attempt={last.attempt} input_tokens={last.input_tokens} "
                f"output_tokens={last.output_tokens} latency_ms={last.latency_ms} "
                f"error_type={result.error_type}"
            )

    run_path = Path("runs") / f"{run_id}.jsonl"
    docs_path = PROJECT_ROOT / "docs" / "day2-run.jsonl"
    docs_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(run_path, docs_path)
    print(f"wrote {run_path}")
    print(f"wrote {docs_path}")


if __name__ == "__main__":
    main()
