from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from promptlab.config import Settings
from promptlab.day1 import (
    CASE_IDS,
    as_int,
    call_ollama,
    load_cases,
    load_prompt,
    main,
    make_record,
)


class _FixedUUID:
    def __str__(self) -> str:
        return "fixed-run"


def test_load_cases_keeps_only_day1_ids() -> None:
    cases = load_cases()
    assert set(cases) == set(CASE_IDS)
    for case_id in CASE_IDS:
        assert "source" in cases[case_id]


def test_load_prompt_has_document_placeholder() -> None:
    prompt = load_prompt()
    assert "{document_text}" in prompt


def test_as_int_accepts_ints_and_rejects_other_values() -> None:
    assert as_int(42) == 42
    assert as_int(True) == 0
    assert as_int("12") == 0
    assert as_int(None) == 0
    assert as_int(3.5, default=9) == 9


def test_make_record_maps_ollama_payload() -> None:
    model_id = Settings.from_env().models["mistral"].model_id
    record = make_record(
        run_id="run-1",
        model_id=model_id,
        case_id="E12",
        temperature=0.0,
        num_predict=256,
        payload={
            "response": "hello",
            "prompt_eval_count": 12,
            "eval_count": 4,
            "done_reason": "stop",
        },
        latency_ms=321,
        error_type=None,
    )
    assert record.provider == "ollama"
    assert record.task == "extraction"
    assert record.prompt_id == "baseline"
    assert record.prompt_version == "v0"
    assert record.input_tokens == 12
    assert record.output_tokens == 4
    assert record.stop_reason == "stop"
    assert record.response_text == "hello"
    assert record.cost_usd == pytest.approx(0.0)
    assert record.timestamp.tzinfo is not None


def test_make_record_handles_missing_and_invalid_fields() -> None:
    model_id = Settings.from_env().models["mistral"].model_id
    record = make_record(
        run_id="run-1",
        model_id=model_id,
        case_id="E11",
        temperature=0.0,
        num_predict=8,
        payload={"prompt_eval_count": True, "response": 123},
        latency_ms=10,
        error_type="TruncatedResponseError",
    )
    assert record.input_tokens == 0
    assert record.output_tokens == 0
    assert record.stop_reason is None
    assert record.response_text is None
    assert record.error_type == "TruncatedResponseError"


def test_call_ollama_posts_generate_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "response": "ok",
                "prompt_eval_count": 7,
                "eval_count": 3,
                "done_reason": "stop",
            }

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> FakeResponse:
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("promptlab.day1.httpx.post", fake_post)

    payload, latency_ms = call_ollama(
        base_url="http://example.invalid",
        model_id="mistral:7b",
        prompt="hello",
        temperature=0.0,
        num_predict=256,
    )
    assert captured["url"] == "http://example.invalid/api/generate"
    assert captured["json"]["options"]["num_predict"] == 256
    assert payload["done_reason"] == "stop"
    assert latency_ms >= 0


def test_main_writes_three_successes_and_one_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    responses: list[tuple[dict[str, object], int]] = [
        ({"response": "a", "prompt_eval_count": 10, "eval_count": 2, "done_reason": "stop"}, 11),
        ({"response": "b", "prompt_eval_count": 20, "eval_count": 3, "done_reason": "stop"}, 22),
        ({"response": "c", "prompt_eval_count": 30, "eval_count": 4, "done_reason": "stop"}, 33),
        ({"response": "d", "prompt_eval_count": 30, "eval_count": 8, "done_reason": "length"}, 5),
    ]

    def fake_call_ollama(**kwargs: Any) -> tuple[dict[str, object], int]:
        return responses.pop(0)

    monkeypatch.setattr("promptlab.day1.call_ollama", fake_call_ollama)
    monkeypatch.setattr("promptlab.day1.uuid.uuid4", lambda: _FixedUUID())

    main()

    path = tmp_path / "runs" / "fixed-run.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["case_id"] for row in rows] == ["E12", "E07", "E11", "E11"]
    assert rows[0]["error_type"] is None
    assert rows[3]["error_type"] == "TruncatedResponseError"
    assert rows[3]["stop_reason"] == "length"
    assert rows[3]["max_output_tokens"] == 8
