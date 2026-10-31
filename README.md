# Data Analyst Agent

Ask questions about a database in plain English. The agent writes SQL, runs it **read-only**, reads the result,
fixes its own mistakes, and answers. Its accuracy is measured on the public [Spider](https://yale-lily.github.io/spider)
text-to-SQL benchmark, including an ablation that shows what each part of the design contributes.

Runs on free Groq models and free hosting. No paid plan is needed anywhere.

> **Status:** code, tests and the benchmark pipeline are done. Benchmark numbers appear in
> [`results/RESULTS.md`](results/RESULTS.md) as the nightly runs finish; this README states only what has been measured.

## How it works

1. **Schema in the prompt.** `CREATE` statements plus three sample rows per table, so the model sees how values are really stored.
2. **`run_sql` tool.** The model tries queries and reads the results or the error message, then corrects itself (up to 5 queries).
3. **Safe execution** (`src/analyst/sqlguard.py`). Model-written SQL is untrusted: the database opens read-only, a SQLite
   authorizer allows only `SELECT`-type operations (no `PRAGMA`, `ATTACH`, writes or extension loading), only one statement
   runs, rows are capped, and a 5 second limit stops runaway queries. Tests try `DROP`, `DELETE`, `PRAGMA`, `ATTACH` and
   multi-statement injection.
4. **Clarifying questions.** In the web app, an ambiguous question gets a short question back instead of a guess.
5. **Final SQL.** The answer ends with `FINAL SQL:`; the web app runs it again and shows the table and the query.

## Evaluation

* **Data:** Spider development set (1,034 questions over 20 databases), sampled reproducibly and spread across databases.
* **Metric:** execution accuracy. The predicted and gold queries must return the same rows (column order and names are
  ignored; row order counts only when the gold query has `ORDER BY`; floats compared to 4 decimals).
  This is close to, but not identical with, Spider's official evaluator.
* **Ablation** (same questions for each configuration):

| Configuration | What changes |
|---|---|
| `oneshot` | Schema only, no tools, one attempt (baseline) |
| `agent_1try` | Tools, but only one query may be run (no self-correction) |
| `agent_nosamples` | Self-correction, schema without sample rows |
| `agent` | Self-correction and sample rows (the full design) |

Runs are resumable and quota-aware (rate limiter, model fallback chain, response cache, daily ledger), so the whole benchmark
finishes over a few nights on Groq's free tier. See `.github/workflows/bench.yml`.

### Limits to keep in mind

* Spider is public and widely studied; a good score matches the field rather than leading it.
* A sample of 200 questions has a margin of error of several percentage points; differences of a few points between
  configurations are not conclusive.
* Scores depend on the free models available on the day, and runs may mix models when a
  daily quota runs out (the per-model counts are recorded in `results/summary.json`).

## Run it

```bash
pip install -e .
python -m unittest discover -s tests -t .            # 33 tests, no network
python scripts/make_demo_db.py                        # builds demo/demo.sqlite (synthetic shop data)
export GROQ_API_KEY=...                               # free key from console.groq.com
python -m analyst ask demo/demo.sqlite "Which country has the most customers?"
python scripts/fetch_spider.py && python -m analyst bench --config agent --n 200
python -m analyst serve                                # web service: /ask, /upload (CSV), /health
```

## Free deployment

`render.yaml` defines a free Render web service. Set `GROQ_API_KEY` as a secret. The service sleeps when idle, so the
portfolio page wakes it when someone opens the demo and keeps visitors engaged while it starts. The service limits each
visitor to 10 questions an hour and the whole demo to 150 a day, to stay within the free model quota.

Uploaded CSVs (max 400 KB) are held in memory for an hour and never written to disk.

## Licence

MIT for the code. Spider is licensed CC BY-SA 4.0 and is downloaded at run time, not redistributed here.
