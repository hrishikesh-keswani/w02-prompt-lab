# Day 3 notes

Run `68ac932f-f6f4-473c-a70f-e2fd75196cda` used `mistral:7b` at `temperature=0.0` with `summarize.v1.md` over S01–S12 and `extract.v2.md` over E01–E12.

- Summarization repair rate: 3 / 12
- Extraction repair rate: 0 / 12
- Example leakage count: 0
- Citation-existence failure count: 0

The most common validation error was an incomplete `EvidenceField` that omitted a required `value` or `status` key on absent fields, sometimes with an extra key the schema forbids. The repair request returned that Pydantic error and asked the model to correct only those issues; S05, S09, and S12 still failed after the single allowed repair.
