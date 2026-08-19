# Methodology

## What is reproduced

FuncBenchGen represents multi-step tool use as traversal over a hidden directed
acyclic graph. Function nodes emit one typed value; edges encode dependencies.
The model sees randomized tool schemas, initial values, and a target, but never
the graph. StateBench preserves synthetic generation, deterministic target
values, exact-answer evaluation, five fixed seeds, randomized tool order, and a
maximum call budget of twice the shortest valid path.

Three frozen conditions vary only the intended difficulty control:

| Condition | Core functions | Max depth | Connected distractors |
|---|---:|---:|---:|
| Easy | 5 | 1 | 0 |
| Deep | 5 | 4 | 0 |
| Connected distractors | 5 | 4 | 10 |

The paper-style calibration is kept separate from the extension. It used opaque
descriptions and the upstream behavior where a wrong input can execute and
propagate an incorrect value. Its full-history results were 2/5 easy, 0/5 deep,
and 0/5 connected.

## Shared compatible interface

The final 75 executions use one interface for all strategies. Tool descriptions
name public inputs and outputs, a premature call returns
`value_not_yet_known`, the model makes one call per response, and only observed
values may be supplied. These changes create a usable operating regime for the
chosen model without revealing hidden edges, node labels, the target path,
ground-truth values, or correctness labels.

Qwen3.6-27B runs through Groq at temperature 0 and top-p 1, with thinking
enabled, a 4,096-token completion allowance, and the original two-times-minimum
call budget. Atomic JSON checkpoints and `--resume` make quota interruptions
recoverable.

## Working-state strategies

1. **Full history** adds no reminder; the model relies on conversation history.
2. **Paper restatement** appends every known value in readable text.
3. **JSON state** appends the same values in a compact key-value object.
4. **Static dependency pruning** retains a value if its exact type is consumed
   anywhere in the public schema set. Because every observed type stayed
   globally consumable, it retained the same state as JSON.
5. **Live dependency pruning** is a post hoc, mechanism-driven follow-up. It
   builds a conservative backward type frontier from the public target and
   unfinished public schemas, keeps one non-conflicting value per still-needed
   type, does not retire failed calls, and emits no suffix after completion.

Both pruning implementations operate only on information visible under the
compatible protocol. The live treatment is reported separately from the
pre-existing four-strategy panel so the original 60 outputs remain immutable.

## Measures and interpretation

Primary outcomes are exact target success and request-level prompt tokens.
Secondary measures include completion tokens, calls, prompt tokens per success,
cumulative bytes added by state suffixes, and trace error occurrences. A trial
can contain a recoverable error and still succeed. Failed trials may also stop
early, so lower total tokens do not automatically mean greater efficiency.

Five fixtures per condition support paired descriptive comparison, not
confidence intervals or statistical-significance claims.
