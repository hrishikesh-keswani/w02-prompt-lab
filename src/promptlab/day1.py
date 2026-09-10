"""Day 1 local Ollama instrumentation run."""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime

import httpx

from promptlab.config import PROJECT_ROOT, Settings
from promptlab.usage import CallRecord, append_record, compute_cost

CASE_IDS = ("E12", "E07", "E11")
PROMPT_ID = "baseline"
PROMPT_VERSION = "v0"
NORMAL_NUM_PREDICT = 256
TRUNCATION_NUM_PREDICT = 8


def load_cases() -> dict[str, dict[str, str]]:
    path = PROJECT_ROOT / "cases" / "extraction.jsonl"
    cases: dict[str, dict[str, str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row["id"] in CASE_IDS:
            cases[row["id"]] = row
    return cases


def load_prompt() -> str:
    return (PROJECT_ROOT / "src" / "prompts" / "baseline.v0.md").read_text(encoding="utf-8")


def call_ollama(
    *,
    base_url: str,
    model_id: str,
    prompt: str,
    temperature: float,
    num_predict: int,
) -> tuple[dict[str, object], int]:
    started = time.perf_counter()
    response = httpx.post(
        f"{base_url}/api/generate",
        json={
            "model": model_id,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
            },
        },
        timeout=180.0,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    response.raise_for_status()
    payload: dict[str, object] = response.json()
    return payload, latency_ms


def as_int(value: object, default: int = 0) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return default


def make_record(
    *,
    run_id: str,
    model_id: str,
    case_id: str,
    temperature: float,
    num_predict: int,
    payload: dict[str, object],
    latency_ms: int,
    error_type: str | None,
) -> CallRecord:
    input_tokens = as_int(payload.get("prompt_eval_count"))
    output_tokens = as_int(payload.get("eval_count"))
    stop_reason = payload.get("done_reason")
    response_text = payload.get("response")
    return CallRecord(
        record_id=str(uuid.uuid4()),
        run_id=run_id,
        timestamp=datetime.now(UTC),
        provider="ollama",
        model_id=model_id,
        task="extraction",
        case_id=case_id,
        prompt_id=PROMPT_ID,
        prompt_version=PROMPT_VERSION,
        attempt=1,
        temperature=temperature,
        max_output_tokens=num_predict,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=None,
        latency_ms=latency_ms,
        cost_usd=compute_cost(model_id, input_tokens, output_tokens),
        stop_reason=str(stop_reason) if stop_reason is not None else None,
        error_type=error_type,
        response_text=response_text if isinstance(response_text, str) else None,
    )


def main() -> None:
    settings = Settings.from_env()
    model_id = settings.models["mistral"].model_id
    temperature = 0.0
    run_id = str(uuid.uuid4())
    cases = load_cases()
    prompt_template = load_prompt()

    print(f"run_id={run_id}")

    for case_id in CASE_IDS:
        prompt = prompt_template.replace("{document_text}", cases[case_id]["source"])
        payload, latency_ms = call_ollama(
            base_url=settings.ollama_base_url,
            model_id=model_id,
            prompt=prompt,
            temperature=temperature,
            num_predict=NORMAL_NUM_PREDICT,
        )
        record = make_record(
            run_id=run_id,
            model_id=model_id,
            case_id=case_id,
            temperature=temperature,
            num_predict=NORMAL_NUM_PREDICT,
            payload=payload,
            latency_ms=latency_ms,
            error_type=None,
        )
        append_record(record, run_id)
        print(
            f"{case_id} input_tokens={record.input_tokens} "
            f"latency_ms={record.latency_ms} stop_reason={record.stop_reason}"
        )

    truncation_prompt = prompt_template.replace("{document_text}", cases["E11"]["source"])
    payload, latency_ms = call_ollama(
        base_url=settings.ollama_base_url,
        model_id=model_id,
        prompt=truncation_prompt,
        temperature=temperature,
        num_predict=TRUNCATION_NUM_PREDICT,
    )
    error_type = "TruncatedResponseError" if payload.get("done_reason") == "length" else None
    truncation_record = make_record(
        run_id=run_id,
        model_id=model_id,
        case_id="E11",
        temperature=temperature,
        num_predict=TRUNCATION_NUM_PREDICT,
        payload=payload,
        latency_ms=latency_ms,
        error_type=error_type,
    )
    append_record(truncation_record, run_id)
    print(
        f"E11 truncation stop_reason={truncation_record.stop_reason} "
        f"error_type={truncation_record.error_type}"
    )
    print(f"wrote runs/{run_id}.jsonl")


if __name__ == "__main__":
    main()
