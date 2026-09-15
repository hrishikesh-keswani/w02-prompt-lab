from promptlab.schemas import TriageOutput
from promptlab.scoring import human_boundary_pass, score_case


def _output(**overrides: object) -> TriageOutput:
    payload: dict[str, object] = {
        "queue": "card_dispute",
        "escalation_required": False,
        "confidence": 0.8,
        "rationale": "Duplicate recognized merchant charge.",
        "draft_reply": "A specialist will review the duplicate charge.",
        "human_review_required": True,
        "customer_outcome": None,
    }
    payload.update(overrides)
    return TriageOutput.model_validate(payload)


def _gold(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "T01",
        "task": "triage",
        "expected_queue": "card_dispute",
        "expected_escalation": False,
    }
    payload.update(overrides)
    return payload


def test_queue_and_escalation_match_gold() -> None:
    records = score_case(
        run_id="r1",
        model_name="mistral",
        prompt_version="v1",
        gold=_gold(),
        output=_output(),
    )
    by_metric = {row.metric: row for row in records}
    assert by_metric["queue"].numerator == 1
    assert by_metric["escalation"].numerator == 1
    assert by_metric["missed_escalation"].numerator == 0
    assert by_metric["unnecessary_escalation"].numerator == 0


def test_missed_escalation_uses_escalation_required() -> None:
    records = score_case(
        run_id="r1",
        model_name="mistral",
        prompt_version="v1",
        gold=_gold(id="T06", expected_queue="escalate", expected_escalation=True),
        output=_output(queue="card_dispute", escalation_required=False),
    )
    by_metric = {row.metric: row for row in records}
    assert by_metric["escalation"].numerator == 0
    assert by_metric["missed_escalation"].numerator == 1
    assert by_metric["unnecessary_escalation"].numerator == 0
    assert by_metric["missed_escalation"].lower_is_better is True


def test_unnecessary_escalation_is_separate() -> None:
    records = score_case(
        run_id="r1",
        model_name="mistral",
        prompt_version="v1",
        gold=_gold(),
        output=_output(escalation_required=True),
    )
    by_metric = {row.metric: row for row in records}
    assert by_metric["escalation"].numerator == 0
    assert by_metric["missed_escalation"].numerator == 0
    assert by_metric["unnecessary_escalation"].numerator == 1


def test_human_review_required_is_not_the_escalation_label() -> None:
    output = _output(escalation_required=False, human_review_required=True)
    records = score_case(
        run_id="r1",
        model_name="mistral",
        prompt_version="v1",
        gold=_gold(expected_escalation=True),
        output=output,
    )
    by_metric = {row.metric: row for row in records}
    assert output.human_review_required is True
    assert by_metric["escalation"].numerator == 0
    assert by_metric["missed_escalation"].numerator == 1


def test_human_boundary_rejects_final_outcome_language() -> None:
    output = _output(
        draft_reply="Your dispute has been approved and the funds will be refunded."
    )
    assert human_boundary_pass(output) is False
    records = score_case(
        run_id="r1",
        model_name="mistral",
        prompt_version="v1",
        gold=_gold(),
        output=output,
    )
    assert next(row.numerator for row in records if row.metric == "human_boundary") == 0


def test_human_boundary_allows_neutral_draft() -> None:
    assert human_boundary_pass(_output()) is True
