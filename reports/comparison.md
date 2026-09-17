# Model Comparison

Run ID: `day5-local-comparison-02`

Provider is `ollama` for both models. Local provider/API charge is `$0.00`. Counts are reported with denominators. Latency uses median and maximum rather than mean. Qwen rows are prompt-transfer results: the same Mistral-developed prompt version, not a Qwen-adapted prompt.

This run used `max_output_tokens=1024` for triage, summarization, and extraction. No call hit `stop_reason=length`.

## Extraction


| Model   | Prompt              | Valid outputs | Metrics                                                                                                                                              | Input tokens/case | Output tokens/case | Median latency | Max latency | n   | Repairs | Retries | Final failures |
| ------- | ------------------- | ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------- | ------------------ | -------------- | ----------- | --- | ------- | ------- | -------------- |
| mistral | extract.v2          | 12/12         | citation_correctness: 74/74 required_evidence_recall: 71/72 status_accuracy: 9/12 unsupported_field_avoidance: 9/12 version_selection_accuracy: 1/1  | 2071.9            | 356.7              | 18843 ms       | 20499 ms    | 12  | 0/12    | 0       | 0              |
| qwen    | extract.v2 transfer | 12/12         | citation_correctness: 73/73 required_evidence_recall: 71/72 status_accuracy: 9/12 unsupported_field_avoidance: 10/12 version_selection_accuracy: 1/1 | 1711.9            | 286.5              | 16408 ms       | 19468 ms    | 12  | 0/12    | 0       | 0              |


Missed recoverable fields and invented/unsupported fields are separate metrics (`required_evidence_recall` vs `unsupported_field_avoidance`). Both models missed the same status labels on E01 (superseded), E04, and E10 (contradictory). Version currency was scored from `select_current_version(...)`, not from a model opinion; both selected E02 as current.

## Summarization


| Model   | Prompt                | Valid outputs | Metrics                                                                                                                                              | Input tokens/case | Output tokens/case | Median latency | Max latency | n   | Repairs | Retries | Final failures |
| ------- | --------------------- | ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------- | ------------------ | -------------- | ----------- | --- | ------- | ------- | -------------- |
| mistral | summarize.v1          | 9/12          | citation_correctness: 0/53 required_evidence_recall: 52/60 status_accuracy: 8/12 unsupported_field_avoidance: 0/12 version_selection_accuracy: 1/1   | 1131              | 323.7              | 12192 ms       | 15368 ms    | 15  | 3/12    | 0       | 3              |
| qwen    | summarize.v1 transfer | 12/12         | citation_correctness: 63/63 required_evidence_recall: 60/60 status_accuracy: 10/12 unsupported_field_avoidance: 9/12 version_selection_accuracy: 1/1 | 699.2             | 244.8              | 12444.5 ms     | 15900 ms    | 12  | 0/12    | 0       | 0              |


Mistral failed schema validation on S05, S09, and S12 after one repair each (3 extra observations, `n=15`). Present-field citations from Mistral were section numbers such as `1` rather than headings such as `1. Document Control`, so citation correctness is 0/53. Qwen returned full headings. That is a prompt-transfer result on `summarize.v1`, not a general claim that Mistral cannot summarize.

## Triage


| Model   | Prompt             | Valid outputs | Metrics                                                                                                                                      | Input tokens/case | Output tokens/case | Median latency | Max latency | n   | Repairs | Retries | Final failures |
| ------- | ------------------ | ------------- | -------------------------------------------------------------------------------------------------------------------------------------------- | ----------------- | ------------------ | -------------- | ----------- | --- | ------- | ------- | -------------- |
| mistral | triage.v1          | 12/12         | escalation: 9/12 human_boundary_compliance: 12/12 missed_escalation: 2/12 ↓ pii_leakage: 0/12 ↓ queue: 10/12 unnecessary_escalation: 1/12 ↓  | 809.8             | 129.8              | 5618.5 ms      | 7856 ms     | 12  | 0/12    | 0       | 0              |
| qwen    | triage.v1 transfer | 12/12         | escalation: 11/12 human_boundary_compliance: 12/12 missed_escalation: 1/12 ↓ pii_leakage: 0/12 ↓ queue: 11/12 unnecessary_escalation: 0/12 ↓ | 677.6             | 110.1              | 5398.5 ms      | 8477 ms     | 12  | 0/12    | 0       | 0              |


Routing misses: Mistral sent T07 to `complaint` and T08 to `account_servicing` (both gold `escalate`), and marked T09 as needing escalation. Qwen still missed T07 and correctly escalated T08. Day 4's prompt choice remains `triage.v1`; `triage.v2` was not re-run.

### Human boundary

`draft_reply` was checked on both configured models (`mistral:7b` and `qwen3:8b`) with `triage.v1`. Human-boundary compliance is 12/12 for each. No committed draft promised a refund, approved or denied a claim, stated that the issue was resolved, or implied a final customer outcome. PII leakage is 0/12 on both models.

## Recommendation


| Task          | Model | Prompt version        | Reason                                                                                                                                      | Reopen if                                                                                                               |
| ------------- | ----- | --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| summarization | qwen  | summarize.v1 transfer | 12/12 valid outputs vs 9/12; required evidence 60/60 vs 52/60; heading citations 63/63 vs 0/53                                              | a Mistral-specific `summarize.v2` that requires full heading citations is measured, or the schema-repair budget changes |
| extraction    | qwen  | extract.v2 transfer   | Recall tied at 71/72 and status tied at 9/12; Qwen avoided unsupported fields 10/12 vs 9/12 and used fewer tokens with lower median latency | contradictory/superseded status accuracy becomes the primary metric, or `extract.v3` is tested                          |
| triage        | qwen  | triage.v1 transfer    | Queue 11/12 vs 10/12 and escalation 11/12 vs 9/12; both models 12/12 human-boundary and 0/12 PII                                            | T07 is fixed by a new prompt version, the one-or-two-case gap reverses on a larger set, or `triage.v2` is re-evaluated  |


These are three separate task decisions. They are not a universal ranking of Qwen over Mistral.

## Limits

- Each task uses 12 cases. Results are directional, not production-scale reliability estimates. An 11/12 versus 10/12 gap is not a universal model ranking.
- Every row names the prompt version it ran. Rows labeled `transfer` ran a prompt developed while working with Mistral, unchanged, on Qwen.
- Untested in this run: `triage.v2` on either model, `extract.v1`, `extract.v3`, any summarization version other than `v1`, Qwen-adapted prompt versions, temperatures other than 0.0.
- No production-volume reliability claim is being made.
- Local Ollama latency depends on lab hardware and is not a cloud SLA. This run used `think=False` on both adapters so Qwen3 would return JSON rather than a thinking trace.
- Local Ollama provider/API charge is `$0.00`. Compare input/output tokens, median/max latency, repairs, retries, and failures rather than invented dollar prices.
