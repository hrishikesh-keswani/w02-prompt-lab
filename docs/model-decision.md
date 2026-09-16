# Model Decision Record

Run ID: `day5-local-comparison-01`

Both evaluated models used `provider = "ollama"` and `cost_usd = 0.0`. Model identity comes from configuration (`MODEL_A` / `MODEL_B`) through `model_id`. This record does not invent cloud token prices.

Day 4 constraint preserved: `triage.v1` remains the triage prompt family. `triage.v2` was not selected then (queue 9/12 vs 10/12, extra output tokens and latency) and was not re-run here.

## Evaluated models

- mistral (`mistral:7b`)
- qwen (`qwen3:8b`)

## Evaluated configurations

- `extraction` — mistral — `extract.v2`
- `extraction` — qwen — `extract.v2 transfer`
- `summarization` — mistral — `summarize.v1`
- `summarization` — qwen — `summarize.v1 transfer`
- `triage` — mistral — `triage.v1`
- `triage` — qwen — `triage.v1 transfer`

## Evidence

Join key for every score row: `run_id`, `case_id`, `task`, `model_id`, `prompt_id`, `prompt_version`. Group metric `version_selection_accuracy` is derived from extracted dates via `select_current_version`, not from a model currency opinion.

| Task | Model | Prompt version | Valid | Quality (selected) | Input tok/case | Output tok/case | Median latency | Max latency | n | Repairs | Failures |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| extraction | mistral | extract.v2 | 12/12 | recall 71/72; citations 74/74; unsupported avoided 9/12; status 9/12 | 2071.9 | 356.7 | 19394.5 ms | 21257 ms | 12 | 0/12 | 0 |
| extraction | qwen | extract.v2 transfer | 12/12 | recall 71/72; citations 73/73; unsupported avoided 10/12; status 9/12 | 1711.9 | 286.5 | 16267 ms | 19909 ms | 12 | 0/12 | 0 |
| summarization | mistral | summarize.v1 | 9/12 | recall 52/60; citations 0/53; status 8/12 | 1131 | 323.7 | 12750 ms | 15882 ms | 15 | 3/12 | 3 |
| summarization | qwen | summarize.v1 transfer | 12/12 | recall 60/60; citations 63/63; status 10/12 | 699.2 | 244.8 | 12981.5 ms | 16373 ms | 12 | 0/12 | 0 |
| triage | mistral | triage.v1 | 12/12 | queue 10/12; escalation 9/12; missed esc. 2/12; unnecessary esc. 1/12; human boundary 12/12; PII 0/12 | 809.8 | 129.8 | 5853 ms | 8023 ms | 12 | 0/12 | 0 |
| triage | qwen | triage.v1 transfer | 12/12 | queue 11/12; escalation 11/12; missed esc. 1/12; unnecessary esc. 0/12; human boundary 12/12; PII 0/12 | 677.6 | 110.1 | 5606 ms | 8930 ms | 12 | 0/12 | 0 |

Human-boundary re-check: both `mistral:7b` and `qwen3:8b` with `triage.v1`. No committed `draft_reply` promised a refund, approved or denied a claim, stated the issue was resolved, or implied a final customer outcome.

## Decision

| Task | Selected model | Prompt version | Reason |
| --- | --- | --- | --- |
| summarization | qwen | summarize.v1 transfer | Schema success 12/12 vs 9/12 and heading-level citations 63/63 vs number-only citations 0/53 on the same prompt |
| extraction | qwen | extract.v2 transfer | Recall and status tied; Qwen avoided more unsupported fields and used fewer tokens with lower median latency |
| triage | qwen | triage.v1 transfer | Better routing and escalation on this 12-case transfer, with equal human-boundary and PII scores |

Do not read this as “Qwen is the better model.” Each row is that model running that prompt version.

## Rejected alternatives

- **Mistral + summarize.v1** for summarization: 3/12 final schema failures (S05, S09, S12) and citations that name section numbers rather than headings.
- **Mistral + extract.v2** for extraction: quality is close enough that origin-model continuity is reasonable, but it did not beat the transfer configuration on recall, status, tokens, or latency.
- **Mistral + triage.v1** for triage: still a viable Day 4 configuration (queue 10/12, human boundary 12/12) but lost T08 and added an unnecessary T09 escalation relative to the Qwen transfer.
- **triage.v2**: rejected on Day 4 (queue 9/12, +400 output tokens, higher latency). Not re-opened by this run.
- **extract.v3 / Qwen-adapted prompts**: not measured. Creating them would be a new prompt version, not a reason to rewrite these transfer results.

## Review triggers

- A new prompt version is committed for either model (especially a Mistral `summarize.v2` that requires full heading citations, or a Qwen-adapted triage prompt aimed at T07).
- The 12-case corpus is replaced or expanded.
- `triage.v2` is re-evaluated under the Day 5 scorer.
- Schema-repair policy or `think=False` changes.
- Human-boundary or PII leakage is no longer 12/12 / 0/12 on either model.
- Version-selection groups stop agreeing with gold after extraction quality changes.
