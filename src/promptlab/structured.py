from __future__ import annotations

import json
from typing import cast

from pydantic import BaseModel, ValidationError

from promptlab.adapters.base import CompletionRequest, ModelAdapter


class StructuredOutputError(Exception):
    """Raised when model output cannot be validated after bounded semantic repair."""


def complete_structured[T: BaseModel](
    adapter: ModelAdapter,
    request: CompletionRequest,
    schema: type[T],
    run_id: str,
    max_repairs: int = 1,
) -> T:
    """Return a schema-validated completion with a bounded semantic repair loop.

    Transport retry remains inside the adapter.
    Schema/content repair belongs here.

    On validation failure, send the validation error text back to the model and
    instruct it to correct only what the error concerns. Do not perform more
    than max_repairs semantic repair attempts.
    """

    current = request
    repairs_used = 0
    while True:
        result = adapter.complete(current, run_id)
        if not result.succeeded:
            raise StructuredOutputError(
                result.error_type or "adapter call failed before schema validation"
            )
        parsed, error_text = _try_validate(schema, result.text)
        if parsed is not None:
            return parsed
        if repairs_used >= max_repairs:
            raise StructuredOutputError(error_text)
        repairs_used += 1
        current = request.model_copy(
            update={
                "user_content": _repair_user_content(
                    original=request.user_content,
                    previous_output=result.text,
                    error_text=error_text,
                )
            }
        )


def _try_validate[T: BaseModel](schema: type[T], text: str | None) -> tuple[T | None, str]:
    if text is None or not text.strip():
        return None, "validation error: empty response"
    candidates = _json_candidates(text)
    if not candidates:
        return None, "validation error: response was not valid JSON"
    errors: list[str] = []
    for candidate in candidates:
        for payload in _expand_payloads(candidate, schema):
            if not isinstance(payload, dict):
                errors.append("validation error: output is not a JSON object")
                continue
            try:
                return schema.model_validate(cast(dict[str, object], payload)), ""
            except ValidationError as exc:
                errors.append(str(exc))
    return None, errors[-1]


def _expand_payloads(value: object, schema: type[BaseModel]) -> list[object]:
    payloads = [value]
    if not isinstance(value, dict) or len(value) != 1:
        return payloads
    key, inner = next(iter(value.items()))
    if not isinstance(inner, dict):
        return payloads
    schema_fields = set(schema.model_fields)
    if key not in schema_fields and schema_fields.intersection(inner.keys()):
        payloads.insert(0, inner)
    return payloads


def _json_candidates(text: str) -> list[object]:
    stripped = _strip_markdown_fence(text.strip())
    values = _decode_objects(stripped)
    instances = [value for value in values if not _looks_like_json_schema(value)]
    return instances or values


def _strip_markdown_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) < 2:
        return text
    body = lines[1:]
    if body and body[-1].strip() == "```":
        body = body[:-1]
    return "\n".join(body).strip()


def _decode_objects(text: str) -> list[object]:
    decoder = json.JSONDecoder()
    values: list[object] = []
    index = 0
    while index < len(text):
        start = text.find("{", index)
        if start < 0:
            break
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            index = start + 1
            continue
        values.append(value)
        index = end
    return values


def _looks_like_json_schema(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    return "$defs" in value or "$schema" in value or (
        "properties" in value and "title" in value and value.get("type") == "object"
    )


def _repair_user_content(
    *,
    original: str,
    previous_output: str | None,
    error_text: str,
) -> str:
    previous = previous_output if previous_output is not None else ""
    return (
        f"{original}\n\n"
        "The previous response failed schema validation.\n\n"
        f"Previous output:\n{previous}\n\n"
        f"Validation error:\n{error_text}\n\n"
        "Correct only what the validation error concerns. "
        "Return a flat JSON object whose keys are the schema fields. "
        "Do not wrap the object under the schema type name. "
        "Return only the corrected JSON object. Do not add commentary."
    )
