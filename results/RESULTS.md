# Spider development-set results (execution accuracy)

| Configuration | Questions | Execution accuracy | LLM calls / q | SQL errors / q | Tokens / q |
|---|---|---|---|---|---|
| agent_1try_v1_s1_n200 | 111 | 78.4% | 2.41 | 0.0 | 2303 |
| agent_1try_v2_gpt-oss-120b_s1_n100 | 1 | 100.0% | 2.0 | 0.0 | 1893 |
| agent_1try_v2_gpt-oss-20b_s1_n100 | 1 | 100.0% | 2.0 | 0.0 | 1915 |
| agent_v1_s1_n200 | 154 | 79.2% | 2.26 | 0.01 | 2195 |
| agent_v2_gpt-oss-120b_s1_n100 | 62 | 85.5% | 2.23 | 0.02 | 2286 |
| agent_v2_gpt-oss-20b_s1_n100 | 63 | 87.3% | 2.22 | 0.02 | 2293 |
| oneshot_v1_s1_n200 | 200 | 82.5% | 1.0 | 0.0 | 536 |
| oneshot_v2_gpt-oss-20b_s1_n100 | 100 | 86.0% | 1.0 | 0.0 | 565 |
