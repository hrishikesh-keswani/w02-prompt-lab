# Day 2 model comparison

Run `c9da9054-f911-4d8b-82c9-526cbee8b638` compared Mistral to Qwen 3 using each model's Ollama default for thinking. Both models received the same baseline prompt (`baseline` / `v0`), cases S01–S12, `task=summarization`, `temperature=0.0`, and `max_output_tokens=256`. The adapter did not send a `think` field, so Mistral leaves thinking unused and Qwen 3 keeps thinking on. Both used `provider=ollama` and were distinguished by configured `model_id`. Local provider/API charge is `$0.00` for both.

| Model | Success | Input tokens | Output tokens | Median latency (ms) | Max latency (ms) |
|---|---|---|---|---|---|
| `mistral:7b` | 12 / 12 | 3,063 | 1,274 | 4,868 | 9,440 |
| `qwen3:8b` | 0 / 12 | 2,667 | 3,072 | 12,178 | 14,462 |

Mistral finished every case on `stop`, with a max latency of 9,440 ms on S01. Qwen hit the 256-token ceiling on every case (`stop_reason=length`, `output_tokens=256`, `error_type=TruncatedResponseError`). Each Qwen failure was a single attempt: truncation was recorded and not retried. That is a cap problem, not a transient failure — Qwen's default thinking consumed the entire budget before a visible answer. A higher shared `max_output_tokens` would be needed if the goal is completed Qwen summaries rather than documenting the truncation.
