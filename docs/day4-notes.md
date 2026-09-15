# Day 4 notes

Run `efebd67e-3275-487d-834f-883c885c6bde` compared `triage.v1` with `triage.v2` on `mistral:7b` at `temperature=0.0`, `max_output_tokens=1024`, and `task=triage` over T01–T12. Both versions used one shared `run_id`, the prompt registry, and `complete_structured` through the Ollama adapter. Local provider/API cost is `$0.00`.

The prompts keep standing queue rules and a general mix-then-escalate check. They no longer name individual mixed-case patterns from the gold set.

triage.v1
queue correct: 10/12
escalation correct: 9/12
missed escalations: 2
unnecessary escalations: 1
human-boundary passes: 12/12

triage.v2
queue correct: 9/12
escalation correct: 9/12
missed escalations: 2
unnecessary escalations: 1
human-boundary passes: 12/12

- Changed-queue count: 2/12 (T06 and T07)
- Output tokens: v1 1,558; v2 1,958; difference +400
- Median latency: v1 6,019 ms; v2 7,673 ms
- Maximum latency: v1 6,621 ms; v2 8,404 ms
- Observation count: 24 (one successful adapter call per case per prompt version; no transport retries or schema repairs)
- Schema success: 12/12 for each version

Output tokens per case:

| Case | v1 | v2 |
|---|---:|---:|
| T01 | 128 | 184 |
| T02 | 121 | 149 |
| T03 | 140 | 166 |
| T04 | 124 | 155 |
| T05 | 131 | 168 |
| T06 | 137 | 166 |
| T07 | 135 | 183 |
| T08 | 147 | 175 |
| T09 | 123 | 163 |
| T10 | 136 | 150 |
| T11 | 119 | 160 |
| T12 | 117 | 139 |

Both versions still validated every case. The misses were the mixed/ambiguous rows: v1 sent T07 to `complaint` and T08 to `account_servicing`, and marked T09 as needing escalation. v2 kept those T07/T08/T09 errors and also sent T06 to `fraud_report` while still setting `escalation_required` true.

The extra `analysis` field did not improve routing on this 12-case set. It used 400 more output tokens and about 1.7 seconds of extra median latency. A one-queue or two-queue gap here is not proof that analysis never helps; under this model, temperature, and case set it did not earn the overhead.
