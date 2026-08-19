# Results and interpretation

## Aggregate outcomes

| Strategy | Success | Avg. prompt | Avg. completion | Avg. calls | Avg. state bytes | Prompt tokens/success |
|---|---:|---:|---:|---:|---:|---:|
| Full history | 12/15 | 7,515.5 | 6,356.8 | 4.67 | 0.0 | 9,394.3 |
| Paper restatement | 13/15 | 8,630.7 | 5,459.9 | 4.87 | 1,227.7 | 9,958.5 |
| JSON state | 15/15 | 8,886.7 | 5,779.9 | 5.27 | 404.7 | 8,886.7 |
| Static dependency pruning | 15/15 | 8,743.6 | 5,542.1 | 5.20 | 404.7 | 8,743.6 |
| Live dependency pruning | 13/15 | 7,484.5 | 5,296.7 | 4.67 | 156.9 | 8,635.9 |

## Exact successes by condition

| Strategy | Easy | Deep | Connected distractors |
|---|---:|---:|---:|
| Full history | 4/5 | 5/5 | 3/5 |
| Paper restatement | 5/5 | 5/5 | 3/5 |
| JSON state | 5/5 | 5/5 | 5/5 |
| Static dependency pruning | 5/5 | 5/5 | 5/5 |
| Live dependency pruning | 5/5 | 5/5 | 3/5 |

The compatible full-history baseline recovered the paper's qualitative
connected-distractor effect: success fell from 5/5 on deep graphs to 3/5 after
adding ten type-compatible distractors. JSON and static pruning recovered all
three full-history failures. Restating every value produced only a one-trial
aggregate gain and no connected-condition gain, qualifying the paper's larger
restatement improvement for this model.

Static pruning and JSON both averaged 404.7 cumulative state bytes. Their small
prompt-token difference comes from different generated trajectories and call
counts, not measured pruning. Live pruning reduced state to 156.9 bytes—61.2%
below JSON—but lost two connected tasks and ended both through
`thinking_output_budget_exhausted`. This shows that the filter activated, while
also showing that smaller external state is not automatically better state.
The evidence is consistent with increased internal reconstruction burden, but
the experiment does not isolate causality and does not establish that a larger
reasoning allowance would solve the failures.

Across all five strategies, traces contained 16 `value_not_yet_known` and seven
thinking-budget-exhaustion occurrences. Some state errors were recoverable, so
these counts are not mutually exclusive failed trials.

Machine-readable sources: `results/final/processed/strategy_summary.csv`,
`strategy_by_config.csv`, `trial_results.csv`, and `failure_counts.csv`.
