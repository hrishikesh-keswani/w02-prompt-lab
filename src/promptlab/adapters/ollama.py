"""Reusable Ollama adapter for every configured local model."""

from __future__ import annotations

import random
import time
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import httpx

from promptlab.adapters.base import CompletionRequest, CompletionResult
from promptlab.config import Settings
from promptlab.errors import (
    PermanentProviderError,
    TransientProviderError,
    TruncatedResponseError,
    UnknownModelError,
)
from promptlab.usage import CallRecord, append_record, compute_cost

MAX_ATTEMPTS = 3
REQUEST_TIMEOUT_SECONDS = 180.0
BACKOFF_BASE_SECONDS = 0.5


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return default


def _extract_text(payload: dict[str, object]) -> str | None:
    response_text = payload.get("response")
    if isinstance(response_text, str):
        return response_text
    message = payload.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
    return None


def _stop_reason(payload: dict[str, object]) -> str | None:
    value = payload.get("done_reason")
    if value is None:
        return None
    return str(value)


def _backoff_seconds(failed_attempt: int) -> float:
    delay = BACKOFF_BASE_SECONDS * (2.0 ** (failed_attempt - 1))
    jitter = 0.25 * random.random()
    return delay + jitter


class OllamaAdapter:
    provider = "ollama"

    def __init__(self, model_id: str, think: bool | None = None) -> None:
        settings = Settings.from_env()
        configured_ids = {model.model_id for model in settings.models.values()}
        if model_id not in configured_ids:
            raise UnknownModelError(model_id)
        self.model_id = model_id
        self._base_url = settings.ollama_base_url
        self._think = think

    def complete(self, request: CompletionRequest, run_id: str) -> CompletionResult:
        records: list[CallRecord] = []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            payload, latency_ms, error_type, retryable = self._attempt(request)
            text = _extract_text(payload)
            record = self._make_record(
                request=request,
                run_id=run_id,
                attempt=attempt,
                payload=payload,
                latency_ms=latency_ms,
                error_type=error_type,
                response_text=text,
            )
            append_record(record, run_id)
            records.append(record)

            if error_type is None:
                return CompletionResult(
                    succeeded=True,
                    text=text,
                    error_type=None,
                    records=records,
                )
            if not retryable or attempt == MAX_ATTEMPTS:
                return CompletionResult(
                    succeeded=False,
                    text=text,
                    error_type=error_type,
                    records=records,
                )
            time.sleep(_backoff_seconds(attempt))

        return CompletionResult(
            succeeded=False,
            text=None,
            error_type=TransientProviderError.__name__,
            records=records,
        )

    def _attempt(
        self,
        request: CompletionRequest,
    ) -> tuple[dict[str, object], int, str | None, bool]:
        started = time.perf_counter()
        body: dict[str, object] = {
            "model": self.model_id,
            "prompt": f"{request.system}\n\n{request.user_content}",
            "stream": False,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_output_tokens,
            },
        }
        if self._think is not None:
            body["think"] = self._think
        try:
            response = httpx.post(
                f"{self._base_url}/api/generate",
                json=body,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.RequestError:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return {}, latency_ms, TransientProviderError.__name__, True

        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code >= 500:
            return {}, latency_ms, TransientProviderError.__name__, True
        if response.status_code >= 400:
            return {}, latency_ms, PermanentProviderError.__name__, False

        raw: Any = response.json()
        payload: dict[str, object] = cast(dict[str, object], raw) if isinstance(raw, dict) else {}
        if _stop_reason(payload) == "length":
            return payload, latency_ms, TruncatedResponseError.__name__, False
        return payload, latency_ms, None, False

    def _make_record(
        self,
        *,
        request: CompletionRequest,
        run_id: str,
        attempt: int,
        payload: dict[str, object],
        latency_ms: int,
        error_type: str | None,
        response_text: str | None,
    ) -> CallRecord:
        input_tokens = _as_int(payload.get("prompt_eval_count"))
        output_tokens = _as_int(payload.get("eval_count"))
        return CallRecord(
            record_id=str(uuid.uuid4()),
            run_id=run_id,
            timestamp=datetime.now(UTC),
            provider="ollama",
            model_id=self.model_id,
            task=request.task,
            case_id=request.case_id,
            prompt_id=request.prompt_id,
            prompt_version=request.prompt_version,
            attempt=attempt,
            temperature=request.temperature,
            max_output_tokens=request.max_output_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=None,
            latency_ms=latency_ms,
            cost_usd=compute_cost(self.model_id, input_tokens, output_tokens),
            stop_reason=_stop_reason(payload),
            error_type=error_type,
            response_text=response_text,
        )
