# Data Analyst Agent

Ask questions about a database in plain English. The agent writes SQL, runs it **read-only**, reads the result,
fixes its own mistakes, and answers. Its accuracy is measured on the public [Spider](https://yale-lily.github.io/spider)
text-to-SQL benchmark, including an ablation that shows what each part of the design contributes.

Runs on free Groq models and free hosting. No paid plan is needed anywhere.

> **Status:** code, tests, benchmark pipeline and the main results are done. A second, unseen sample (seed 2) and two ablation
> arms are still running on the free daily quota; the full tables are in [`results/RESULTS.md`](results/RESULTS.md).

## Results (Spider dev set, two 100-question samples, prompt v2)

Each cell: sample 1 / sample 2, then the pooled score over all 200.

| Configuration | `gpt-oss-120b` | `gpt-oss-20b` | Tokens / question |
|---|---|---|---|
| One shot, no tools | 82 / 77 (79.5%) | 86 / 77 (81.5%) | ~560 |
| Lean: one query, one retry on error (the live demo) | 81 / 79 (80.0%) | 88 / 81 (84.5%) | ~960 |
| Agent, self-correcting (up to 5 tries) | 81 / 78 (79.5%) | 86 / 80 (83.0%) | ~2,300 |
| Five candidates, majority result | 82 / 75 (78.5%) | 88 / 78 (83.0%) | ~2,800 |

Ablations (sample 1 only):

| Configuration | `gpt-oss-120b` | `gpt-oss-20b` | Tokens / question |
|---|---|---|---|
| Agent, one try only | 82% | 84% | ~2,300 |
| Agent, no sample rows in the prompt | 83% | 87% | ~1,600 |

* **The approaches tie.** On 100 questions a difference of a few points is noise (the second sample scored 3 to 11 points lower than the first). One try matches five, and dropping the sample rows costs nothing while saving ~30% of tokens. Tools and
  retries do not raise accuracy on Spider. The agent hits ~0.01 SQL errors per question, so retry-on-error rarely fires:
  wrong answers are mostly valid SQL returning the wrong rows. Lean matches the agent at about 40% of its tokens, which is why the demo uses it.
* **Agreement predicts correctness** (sample 1). When all five candidates return the same rows, accuracy was 90.6% (120b, 77 of 85) and 94.4% (20b, 68 of 72);
  when they split, 33.3% (5 of 15) and 71.4% (20 of 28). That is a usable confidence signal.
* **History.** The first run (prompt v1, kept in `results/*_v1_*`) had the agent ~3 points behind the baseline. The failure
  analysis (`python -m analyst analyze`) found 13 of 32 wrong answers added helpful extra columns that strict scoring rejects,
  plus a parsing bug. Both were fixed in prompt v2.
* The live web app runs the single agent, not five-way voting (too expensive for the free quota).

## How it works

1. **Schema in the prompt.** `CREATE` statements plus three sample rows per table, so the model sees how values are really stored.
2. **Two ways to run it.** The *web app* uses the cheap `lean` mode: one model call, the query runs read-only, and the model is asked
   again only if the query errors or returns no rows (about a quarter of the tokens of the tool-using agent). The *benchmark* also
   measures a tool-using `agent` (a `run_sql` tool, up to 5 queries) and five-candidate `vote`, and found no accuracy gain from them.
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
