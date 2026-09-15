from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TaskName = Literal["triage", "summarization", "extraction"]
FieldStatus = Literal["present", "absent", "ambiguous"]
DocumentStatus = Literal["valid", "contradictory", "superseded", "unsupported"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceField(StrictModel):
    value: str | list[str] | None
    status: FieldStatus
    citation: str | None = None


class TriageOutput(StrictModel):
    queue: Literal[
        "card_dispute",
        "fraud_report",
        "account_servicing",
        "lending",
        "complaint",
        "escalate",
        "unsupported",
    ]
    escalation_required: bool
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    draft_reply: str
    human_review_required: Literal[True]
    customer_outcome: None = None


class TriageOutputWithAnalysis(TriageOutput):
    analysis: str


class SummarizationOutput(StrictModel):
    document_status: DocumentStatus
    title: EvidenceField
    version: EvidenceField
    effective_date: EvidenceField
    purpose: EvidenceField
    required_steps: EvidenceField
    exceptions: EvidenceField

    def evidence_fields(self) -> dict[str, EvidenceField]:
        return {
            "title": self.title,
            "version": self.version,
            "effective_date": self.effective_date,
            "purpose": self.purpose,
            "required_steps": self.required_steps,
            "exceptions": self.exceptions,
        }


class PolicyExtraction(StrictModel):
    document_status: DocumentStatus
    policy_name: EvidenceField
    version: EvidenceField
    effective_date: EvidenceField
    jurisdictions: EvidenceField
    beneficial_ownership_threshold: EvidenceField
    review_frequency: EvidenceField
    required_documents: EvidenceField

    def evidence_fields(self) -> dict[str, EvidenceField]:
        return {
            "policy_name": self.policy_name,
            "version": self.version,
            "effective_date": self.effective_date,
            "jurisdictions": self.jurisdictions,
            "beneficial_ownership_threshold": self.beneficial_ownership_threshold,
            "review_frequency": self.review_frequency,
            "required_documents": self.required_documents,
        }


OUTPUT_SCHEMAS: dict[TaskName, type[StrictModel]] = {
    "triage": TriageOutput,
    "summarization": SummarizationOutput,
    "extraction": PolicyExtraction,
}


def schema_description(model: type[BaseModel]) -> str:
    """Return a compact, prompt-ready description of a Pydantic model.

    The text is derived from the model's JSON schema so prompts do not keep a
    second handwritten copy of the output shape. Nested definitions are listed
    separately. Raw JSON Schema is not returned; models tend to echo it.
    """

    schema = model.model_json_schema()
    defs = schema.get("$defs", {})
    lines: list[str] = []
    _append_object_description(
        lines,
        str(schema.get("title") or model.__name__),
        schema,
        defs,
    )
    for def_name, def_schema in defs.items():
        if isinstance(def_schema, dict):
            lines.append("")
            _append_object_description(lines, str(def_name), def_schema, defs)
    return "\n".join(lines)


def _append_object_description(
    lines: list[str],
    title: str,
    node: dict[str, Any],
    defs: dict[str, Any],
) -> None:
    extra = node.get("additionalProperties", True)
    extra_note = ", additional properties forbidden" if extra is False else ""
    lines.append(
        f"{title} (JSON object{extra_note}). "
        f"Return this object directly; do not wrap it in a key named {title}."
    )
    properties = node.get("properties", {})
    required = set(node.get("required", []))
    if not properties:
        lines.append("- (no properties)")
        return
    for name, prop in properties.items():
        if not isinstance(prop, dict):
            lines.append(f"- {name}: unknown")
            continue
        req = "required" if name in required else "optional"
        default_note = ""
        if "default" in prop:
            default_note = f", default {json.dumps(prop['default'])}"
        type_text = _type_text(prop, defs)
        constraint_note = _constraint_note(prop)
        lines.append(f"- {name}: {type_text} ({req}{default_note}{constraint_note})")


def _type_text(node: dict[str, Any], defs: dict[str, Any]) -> str:
    if "$ref" in node:
        return str(node["$ref"]).rsplit("/", 1)[-1]
    if "const" in node:
        return json.dumps(node["const"])
    if "enum" in node:
        values = ", ".join(json.dumps(value) for value in node["enum"])
        return f"one of {values}"
    for key in ("anyOf", "oneOf"):
        options = node.get(key)
        if isinstance(options, list) and options:
            parts = [
                _type_text(option, defs) if isinstance(option, dict) else str(option)
                for option in options
            ]
            return " | ".join(parts)
    if node.get("type") == "array":
        items = node.get("items", {})
        item_text = _type_text(items, defs) if isinstance(items, dict) else "any"
        return f"array of {item_text}"
    typ = node.get("type")
    if isinstance(typ, list):
        return " | ".join(str(item) for item in typ)
    if typ == "null":
        return "null"
    if typ:
        return str(typ)
    return "any"


def _constraint_note(node: dict[str, Any]) -> str:
    notes: list[str] = []
    if "minimum" in node:
        notes.append(f">= {node['minimum']}")
    if "maximum" in node:
        notes.append(f"<= {node['maximum']}")
    if "minLength" in node:
        notes.append(f"minLength {node['minLength']}")
    if "maxLength" in node:
        notes.append(f"maxLength {node['maxLength']}")
    if not notes:
        return ""
    return ", " + ", ".join(notes)

