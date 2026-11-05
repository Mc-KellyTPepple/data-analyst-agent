# Spider development-set results (execution accuracy)

| Configuration | Questions | Execution accuracy | LLM calls / q | SQL errors / q | Tokens / q |
|---|---|---|---|---|---|
| agent_1try_v1_s1_n200 | 111 | 78.4% | 2.41 | 0.0 | 2303 |
| agent_1try_v2_gpt-oss-120b_s1_n100 | 1 | 100.0% | 2.0 | 0.0 | 1893 |
| agent_1try_v2_gpt-oss-20b_s1_n100 | 1 | 100.0% | 2.0 | 0.0 | 1915 |
| agent_v1_s1_n200 | 154 | 79.2% | 2.26 | 0.01 | 2195 |
| agent_v2_gpt-oss-120b_s1_n100 | 100 | 81.0% | 2.19 | 0.01 | 2294 |
| agent_v2_gpt-oss-120b_s2_n100 | 100 | 78.0% | 2.19 | 0.0 | 2327 |
| agent_v2_gpt-oss-20b_s1_n100 | 100 | 86.0% | 2.22 | 0.02 | 2355 |
| agent_v2_gpt-oss-20b_s2_n100 | 72 | 81.9% | 2.24 | 0.03 | 2317 |
| lean_v2_gpt-oss-120b_s1_n100 | 100 | 81.0% | 1.05 | 0.0 | 956 |
| lean_v2_gpt-oss-120b_s2_n100 | 100 | 79.0% | 1.02 | 0.0 | 937 |
| lean_v2_gpt-oss-20b_s1_n100 | 100 | 88.0% | 1.08 | 0.03 | 983 |
| lean_v2_gpt-oss-20b_s2_n100 | 100 | 81.0% | 1.07 | 0.03 | 994 |
| oneshot_v1_s1_n200 | 200 | 82.5% | 1.0 | 0.0 | 536 |
| oneshot_v2_gpt-oss-120b_s1_n100 | 100 | 82.0% | 1.0 | 0.0 | 557 |
| oneshot_v2_gpt-oss-120b_s2_n100 | 100 | 77.0% | 1.0 | 0.0 | 570 |
| oneshot_v2_gpt-oss-20b_s1_n100 | 100 | 86.0% | 1.0 | 0.0 | 565 |
| oneshot_v2_gpt-oss-20b_s2_n100 | 100 | 77.0% | 1.0 | 0.0 | 569 |
| vote_v2_gpt-oss-120b_s1_n100 | 100 | 82.0% | 5.0 | 0.01 | 2821 |
| vote_v2_gpt-oss-120b_s2_n100 | 21 | 71.4% | 5.0 | 0.0 | 2459 |
| vote_v2_gpt-oss-20b_s1_n100 | 100 | 88.0% | 5.0 | 0.23 | 2863 |
